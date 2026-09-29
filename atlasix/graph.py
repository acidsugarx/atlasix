"""networkx layer over the edges table: neighbors, cycles, shortest path, DOT export."""
from __future__ import annotations

import sqlite3

import networkx as nx


def build_graph(conn: sqlite3.Connection, rels: list[str] | None = None) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for row in conn.execute("SELECT id,kind,path,name,line FROM entities"):
        g.add_node(row["id"], kind=row["kind"], path=row["path"], name=row["name"], line=row["line"])
    q = "SELECT src_id,dst_id,rel FROM edges WHERE dst_id IS NOT NULL"
    if rels:
        q += f" AND rel IN ({','.join('?' * len(rels))})"
        for row in conn.execute(q, rels):
            g.add_edge(row["src_id"], row["dst_id"], rel=row["rel"])
    else:
        for row in conn.execute(q):
            g.add_edge(row["src_id"], row["dst_id"], rel=row["rel"])
    return g


def label(g: nx.MultiDiGraph, nid: int) -> str:
    d = g.nodes[nid]
    return f"{d['kind']}:{d['name']} ({d['path']}:{d.get('line') or 0})"


def subgraph(g: nx.MultiDiGraph, start: int, depth: int) -> nx.MultiDiGraph:
    seen = {start}
    frontier = {start}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            nxt.update(g.successors(n))
            nxt.update(g.predecessors(n))
        nxt -= seen
        seen |= nxt
        frontier = nxt
    return g.subgraph(seen).copy()


def to_dot(g: nx.MultiDiGraph) -> str:
    lines = ["digraph atlasix {"]
    for nid, d in g.nodes(data=True):
        esc = label(g, nid).replace('"', '\\"')
        lines.append(f'  {nid} [label="{esc}"];')
    for u, v, d in g.edges(data=True):
        lines.append(f'  {u} -> {v} [label="{d.get("rel", "")}"];')
    lines.append("}")
    return "\n".join(lines)


def cycles(g: nx.MultiDiGraph) -> list[list[int]]:
    return [c for c in nx.simple_cycles(g) if len(c) > 1]


def shortest_path(g: nx.MultiDiGraph, a: int, b: int) -> list[int] | None:
    try:
        return nx.shortest_path(g, a, b)
    except nx.NetworkXNoPath:
        return None
    except nx.NodeNotFound:
        return None
