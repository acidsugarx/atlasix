"""MCP server: expose atlasix as first-class tools for any MCP client (stdio).

Run: atlasix mcp   (server operates on its working directory)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
except ModuleNotFoundError:  # mcp 2.x: FastMCP → MCPServer
    from mcp.server.mcpserver import MCPServer as _Server

mcp = _Server("atlasix")



def _run(*args: str) -> str:
    r = subprocess.run(
        [sys.executable, "-m", "atlasix", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=Path.cwd(),
    )
    out = r.stdout.strip()
    err = r.stderr.strip()
    return out + (f"\n[stderr] {err}" if err else "") or "(no output)"


@mcp.tool()
def build(no_vectors: bool = False) -> str:
    """(Re)build the atlasix index of this repo. Full rebuild; ~seconds without vectors."""
    return _run("build", *(["--no-vectors"] if no_vectors else []))


@mcp.tool()
def profile() -> str:
    """Domain-free repo summary: extensions, dir tree, entity kinds, unresolved refs, duplicate clusters."""
    return _run("profile")


@mcp.tool()
def show(path: str, resolved: bool = False) -> str:
    """Entities of a file (optionally with edges expanded to their targets)."""
    return _run("show", path, *(["--resolved"] if resolved else []))


@mcp.tool()
def who_uses(target: str) -> str:
    """Reverse references: everything that references the given name-or-path (2 hops)."""
    return _run("who-uses", target)


@mcp.tool()
def search(text: str, top: int = 5) -> str:
    """Hybrid BM25+vector semantic search over repo chunks (RU/EN)."""
    return _run("search", text, "--top", str(top))


@mcp.tool()
def graph(target: str = "", depth: int = 1, cycles: bool = False, dot: bool = False,
          path_between: list[str] | None = None) -> str:
    """Entity graph: neighborhood of `target` up to `depth` hops; cycles; shortest path A→B; DOT export."""
    args = ["graph"]
    if cycles:
        args.append("--cycles")
    elif path_between:
        args += ["--path-between", *path_between]
    else:
        args += [target, "--depth", str(depth)]
    if dot:
        args.append("--dot")
    return _run(*args)


@mcp.tool()
def duplicates() -> str:
    """Copy-paste clusters of text units (e.g. identical CI job scripts)."""
    return _run("duplicates")


@mcp.tool()
def lint() -> str:
    """Run declarative rules over the index. Errors mean broken references — must be fixed."""
    return _run("lint")


@mcp.tool()
def pack_import(name: str = "", frm: str = "", force: bool = False) -> str:
    """Install a domain pack (built-in registry, or private via --from dir/git URL), then run build+lint."""
    args = ["pack", "import"]
    if name:
        args.append(name)
    if frm:
        args += ["--from", frm]
    if force:
        args.append("--force")
    return _run(*args)


@mcp.tool()
def bootstrap_hint() -> str:
    """Valid pack.yaml + rules examples for authoring a custom domain pack."""
    return _run("bootstrap-hint")


def serve():
    mcp.run()


if __name__ == "__main__":
    serve()
