"""Indexer: walk files, run extractors, build edges, text_units, duplicate clusters, chunks+embeddings."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
from pathlib import Path

from . import pack as packmod
from . import schema

VAR_RE = re.compile(r"\$\{?\w+\}?")


def normalize_unit(text: str) -> str:
    lines = [VAR_RE.sub("$VAR", ln).rstrip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def unit_hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _shingles(norm: str, k: int = 3) -> set[tuple[str, ...]]:
    lines = [ln for ln in norm.splitlines() if ln.strip()]
    if len(lines) < k:
        return {tuple(lines)} if lines else set()
    return {tuple(lines[i : i + k]) for i in range(len(lines) - k + 1)}


class Indexer:
    def __init__(self, root: Path, pk: packmod.Pack, with_vectors: bool = True, atlas_dir: Path | None = None):
        self.root = root
        self.pack = pk
        self.with_vectors = with_vectors
        self.db_path = (atlas_dir if atlas_dir is not None else root / ".atlas") / "index.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = schema.rebuild(self.db_path)
        self._entity_by_file: dict[str, list[int]] = {}
        self._file_entity: dict[str, int] = {}

    # ------------------------------------------------------------------ build

    def build(self) -> dict:
        stats = {"files": 0, "entities": 0, "edges": 0, "unresolved": 0, "text_units": 0, "clusters": 0, "vectors": False}
        files = sorted(
            p for p in self.root.rglob("*")
            if p.is_file() and ".atlas" not in p.relative_to(self.root).parts and not self._ignored(p)
        )
        ins_file = self.conn.execute
        for p in files:
            rel = p.relative_to(self.root).as_posix()
            status = "ok"
            try:
                specs = list(self.pack.spec_for_file(rel))
                if specs:
                    for spec in specs:
                        try:
                            items = packmod.extract(spec, p, rel)
                        except Exception:
                            status = "parse_error"
                            continue
                        for name, line, fields in items:
                            self.conn.execute(
                                "INSERT INTO entities(kind,path,name,line,data_json) VALUES(?,?,?,?,?)",
                                (spec.kind, rel, name, line, json.dumps(fields, ensure_ascii=False, default=str)),
                            )
                            self._entity_by_file.setdefault(rel, []).append(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            except Exception:
                status = "error"
            # core 'file' entity
            self.conn.execute("INSERT INTO entities(kind,path,name,line,data_json) VALUES('file',?,? ,NULL,'{}')", (rel, rel))
            self._file_entity[rel] = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.conn.execute("INSERT OR REPLACE INTO files(path,parse_status) VALUES(?,?)", (rel, status))
            stats["files"] += 1
        stats["entities"] = self.conn.execute("SELECT count(*) FROM entities").fetchone()[0]
        self._build_edges()
        stats["edges"] = self.conn.execute("SELECT count(*) FROM edges").fetchone()[0]
        stats["unresolved"] = self.conn.execute("SELECT count(*) FROM edges WHERE dst_id IS NULL").fetchone()[0]
        self._build_text_units()
        stats["text_units"] = self.conn.execute("SELECT count(*) FROM text_units").fetchone()[0]
        self._cluster_duplicates()
        stats["clusters"] = self.conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'cluster:%'").fetchone()[0]
        ok = self._build_chunks()
        stats["vectors"] = bool(ok and self.with_vectors)
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('built','1')")
        self.conn.commit()
        return stats

    def _ignored(self, p: Path) -> bool:
        rel = p.relative_to(self.root)
        return any(part in (".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".atlas") for part in rel.parts)

    # ------------------------------------------------------------------ edges

    def _entities(self):
        return self.conn.execute("SELECT id,kind,path,name,line,data_json FROM entities").fetchall()

    def _build_edges(self):
        rows = self._entities()
        rows_all = rows
        by_name: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_name.setdefault(row["name"], []).append(row)

        file_level = [r for r in self.pack.refs if r.field == "raw_regex"]
        name_level = [r for r in self.pack.refs if r.field != "raw_regex"]
        for ref in file_level + name_level:
            for row in rows:
                if row["kind"] != ref.entity:
                    continue
                if ref.field == "raw_regex":
                    fp = self.root / row["path"]
                    if not fp.is_file():
                        continue
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    for m in re.finditer(ref.pattern, text):
                        raw = m.group(0)
                        target = m.group(1) if m.groups() else raw
                        self._add_edge(ref, row, target, raw, by_name, rows_all)
                elif ref.pattern:
                    data = json.loads(row["data_json"])
                    val = data.get(ref.field)
                    if val is None:
                        continue
                    text = "\n".join(val) if isinstance(val, list) else str(val)
                    for m in re.finditer(ref.pattern, text):
                        raw = m.group(0)
                        target = next((g for g in reversed(m.groups() or ()) if g is not None), raw)
                        self._add_edge(ref, row, target, raw, by_name)
                else:
                    data = json.loads(row["data_json"])
                    val = data.get(ref.field)
                    if val is None:
                        continue
                    vals = val if isinstance(val, list) else [val]
                    for v in vals:
                        self._add_edge(ref, row, str(v), str(v), by_name, rows_all)

    def _include_closure(self, path: str, max_depth: int = 10) -> set:
        """Files reachable from `path` via file-level include edges (incl. itself)."""
        seen, frontier = {path}, [path]
        for _ in range(max_depth):
            nxt = []
            for f in frontier:
                for (dst_id,) in self.conn.execute(
                    "SELECT dst_id FROM edges ed JOIN entities e ON e.id=ed.dst_id "
                    "WHERE ed.src_id=(SELECT id FROM entities WHERE kind='file' AND path=?) "
                    "AND e.kind='file'", (f,)
                ):
                    row = self.conn.execute("SELECT path FROM entities WHERE id=?", (dst_id,)).fetchone()
                    if row and row["path"] not in seen:
                        seen.add(row["path"])
                        nxt.append(row["path"])
            if not nxt:
                break
            frontier = nxt
        return seen

    def _add_edge(self, ref, src_row, target, raw, by_name, rows_all=None):
        dst = None
        if ref.resolve == "same_doc_dict":
            cands = [r for r in by_name.get(target, []) if r["path"] == src_row["path"]]
            if cands:
                dst = cands[0]["id"]
        elif ref.resolve == "repo_path":
            norm = target.replace("\\", "/").split("@")[0]
            while norm.startswith("./") or norm.startswith("/"):
                norm = norm[1:] if norm.startswith("/") else norm[2:]
            fid = self._file_entity.get(norm)
            if fid is None:
                for fpath, cand in self._file_entity.items():
                    if fpath == norm or fpath.endswith("/" + norm):
                        fid = cand
                        break
            if fid is not None:
                dst = fid
        elif ref.resolve == "global_name":
            for r in by_name.get(target, []):
                self.conn.execute(
                    "INSERT INTO edges(src_id,dst_id,rel,raw_ref) VALUES(?,?,?,?)",
                    (src_row["id"], r["id"], ref.rel, raw),
                )
            return
        elif ref.resolve == "include_aware":
            files = self._include_closure(src_row["path"])
            cands = [r for r in by_name.get(target, []) if r["path"] in files and r["kind"] == src_row["kind"]]
            if cands:
                dst = cands[0]["id"]
        elif ref.resolve == "same_doc_anchor":
            cands = []
            for r in rows_all:
                if r["path"] == src_row["path"] and json.loads(r["data_json"]).get("anchor") == target:
                    cands.append(r)
            if cands:
                dst = cands[0]["id"]
        elif ref.resolve == "entity_name":
            cands = [r for r in by_name.get(target, []) if r["kind"] == ref.to]
            if cands:
                dst = cands[0]["id"]
        # resolve: none → keep raw only; unresolved → dst NULL + raw_ref (lint data)
        self.conn.execute(
            "INSERT INTO edges(src_id,dst_id,rel,raw_ref) VALUES(?,?,?,?)",
            (src_row["id"], dst, ref.rel, raw),
        )

    # ------------------------------------------------------------- text units

    def _build_text_units(self):
        if not self.pack.text_unit:
            return
        kind, field = self.pack.text_unit
        for row in self._entities():
            if row["kind"] != kind:
                continue
            data = json.loads(row["data_json"])
            val = data.get(field)
            if val is None:
                continue
            text = "\n".join(val) if isinstance(val, list) else str(val)
            norm = normalize_unit(text)
            if len([l for l in norm.splitlines() if l.strip()]) < 1:
                continue
            self.conn.execute(
                "INSERT INTO text_units(entity_id,path,line,normalized_hash,text) VALUES(?,?,?,?,?)",
                (row["id"], row["path"], row["line"], unit_hash(norm), text),
            )

    def _cluster_duplicates(self):
        rows = self.conn.execute("SELECT id,entity_id,path,normalized_hash,text FROM text_units").fetchall()
        n = len(rows)
        parent = list(range(n))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        def union(i, j):
            parent[find(i)] = find(j)
        sh = []
        for r in rows:
            norm = normalize_unit(r["text"])
            sh.append(_shingles(norm) if len(norm.splitlines()) >= self.pack.dup_min_lines else None)
        hashes = [r["normalized_hash"] for r in rows]
        by_hash = {}
        for i, h in enumerate(hashes):
            by_hash.setdefault(h, []).append(i)
        for group in by_hash.values():
            for j in group[1:]:
                union(group[0], j)
        # near-dups pairwise among candidates with shingles
        cand = [i for i in range(n) if sh[i]]
        for ai in range(len(cand)):
            i = cand[ai]
            for bi in range(ai + 1, len(cand)):
                j = cand[bi]
                si, sj = sh[i], sh[j]
                if not si or not sj:
                    continue
                inter = len(si & sj)
                if not inter:
                    continue
                jac = inter / (len(si) + len(sj) - inter)
                if jac >= self.pack.dup_threshold:
                    union(i, j)
        clusters = {}
        for i in range(n):
            clusters.setdefault(find(i), []).append(i)
        cid = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            cid += 1
            key = f"cluster:{cid}"
            payload = json.dumps([rows[i]["id"] for i in members])
            self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, payload))
        # entity_id set for rules
        self.conn.execute("DELETE FROM meta WHERE key='dup_entity_ids'")
        dups = set()
        for members in clusters.values():
            if len(members) >= 2:
                for i in members:
                    dups.add(rows[i]["entity_id"])
        self.conn.execute("INSERT INTO meta(key,value) VALUES('dup_entity_ids',?)", (json.dumps(sorted(dups)),))

    # ----------------------------------------------------------------- chunks

    def _build_chunks(self):
        docs = []
        for row in self.conn.execute(
            "SELECT tu.id, e.kind, e.path, e.name, tu.text FROM text_units tu JOIN entities e ON e.id=tu.entity_id"
        ).fetchall():
            docs.append(("text_units", row["id"], f"{row['kind']}:{row['name']} ({row['path']})\n{row['text']}"))
        for row in self.conn.execute(
            "SELECT id,kind,path,name,line,data_json FROM entities WHERE kind != 'file'"
        ).fetchall():
            data = json.loads(row["data_json"])
            if not data:
                continue
            body = "\n".join(f"{k}: {v}" for k, v in data.items())
            docs.append(("entities", row["id"], f"{row['kind']}:{row['name']} {row['path']}:{row['line'] or 0}\n{body}"))
        for ref_table, ref_id, text in docs:
            self.conn.execute(
                "INSERT INTO chunks(ref_table,ref_id,text,vec) VALUES(?,?,?,NULL)", (ref_table, ref_id, text)
            )
        if not self.with_vectors or not schema.VEC_OK:
            self.conn.commit()
            return False
        try:
            from fastembed import TextEmbedding

            from . import config

            name, cache_dir = config.embedding_model()
            model = TextEmbedding(name, cache_dir=cache_dir) if cache_dir else TextEmbedding(name)
            ids = [r[0] for r in self.conn.execute("SELECT id FROM chunks").fetchall()]
            texts = [r[0] for r in self.conn.execute("SELECT text FROM chunks").fetchall()]
            vecs = list(model.embed(texts))
            if vecs and not schema.ensure_vec_table(self.conn, len(vecs[0])):
                self.conn.commit()
                return False
            for cid, v in zip(ids, vecs):
                blob = struct.pack(f"{len(v)}f", *v.tolist() if hasattr(v, "tolist") else v)
                self.conn.execute("UPDATE chunks SET vec=? WHERE id=?", (blob, cid))
                self.conn.execute("INSERT INTO vec_chunks(chunk_id,embedding) VALUES(?,?)", (cid, blob))
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            return False
