"""atlasix CLI."""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from . import graph as gmod
from . import pack as packmod
from . import rules as rmod
from . import schema
from .indexer import Indexer

DEFAULT_PACK = """version: 1
# Atlasix domain pack. Authored by an LLM (see `atlasix bootstrap-hint`).
# Structural facts are parsed deterministically; this file is the only
# place where domain knowledge lives.
entity_types: {}
references: {}
text_units: {}
duplicates:
  min_lines: 5
  threshold: 0.85
"""

HINT = """\
# ---------------------------------------------------------------- pack.yaml
version: 1
entity_types:
  job:                              # map any structured file to entities
    files: ["**/*.yml"]
    extractor: yaml_jobs            # yaml_jobs | yaml_keys | regex | json_pointer | line_symbols
    fields: [stage, image, script]  # keys copied into entity data
  symbol:
    files: ["**/*.nu", "**/*.sh"]
    extractor: line_symbols
    pattern: '^\\s*export def\\s+(?P<name>\\w+)'
references:
  extends:
    from: {entity: job, field: extends}
    resolve: same_doc_dict          # same_doc_dict | repo_path | none
  includes:
    from: {entity: file, field: raw_regex, pattern: '(\\S+\\.yml)@(\\S+)'}
    resolve: repo_path
text_units:
  unit: {entity: job, field: script}
duplicates:
  min_lines: 5
  threshold: 0.85

# ---------------------------------------------------------------- rules/dup.yaml
id: duplicated-jobs
severity: warn
match:
  kind: job
  path_glob: "pipelines/**"
where: "index.in_duplicate_cluster(fact.id)"
message: "{kind} {name} ({path}) belongs to a duplicate cluster — extract shared block"

# ---------------------------------------------------------------- rules/unresolved.yaml
id: unresolved-ref
severity: error
match: {kind: file}
where: "len([r for r in index.unresolved_refs if r['name'] == fact.name]) > 0"
message: "{path} has unresolved references"
"""


def _root() -> Path:
    return Path.cwd()


def _atlas_dir(root: Path, create: bool = False) -> Path:
    """Repo `.atlas/` if present, else per-repo dir under ~/.atlasix/repos/ (no repo pollution)."""
    local = root / ".atlas"
    if local.exists():
        return local
    import hashlib
    import re as _re

    from . import config

    slug = _re.sub(r"[^A-Za-z0-9._-]+", "-", root.resolve().name) or "repo"
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:8]
    d = config.ATLASIX_HOME / "repos" / f"{slug}-{digest}"
    if create and not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        (d / "root.txt").write_text(str(root.resolve()), encoding="utf-8")
    elif create:
        d.mkdir(parents=True, exist_ok=True)
        marker = d / "root.txt"
        if not marker.exists():  # state dir created before markers existed
            marker.write_text(str(root.resolve()), encoding="utf-8")
    return d


def _open_db(root: Path):
    db = _atlas_dir(root) / "index.db"
    if not db.exists():
        print("no index.db (repo .atlas/ or ~/.atlasix/repos/) — run `atlasix build` first", file=sys.stderr)
        sys.exit(2)
    conn = schema.connect(db)
    schema.load_vec(conn)
    return conn


def _load_pack(root: Path) -> packmod.Pack:
    p = _atlas_dir(root) / "pack.yaml"
    if not p.exists():
        return packmod.Pack({"version": 1}, p)
    try:
        return packmod.load_pack(p)
    except packmod.PackError as e:
        print(f"pack error: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_repos(args):
    from . import config

    repos = config.ATLASIX_HOME / "repos"
    if not repos.is_dir():
        print(f"no state dirs in {repos}")
        return
    entries = []
    for d in sorted(repos.iterdir()):
        if not d.is_dir():
            continue
        marker = d / "root.txt"
        root = marker.read_text(encoding="utf-8").strip() if marker.exists() else None
        state = "unknown" if root is None else ("ok" if Path(root).is_dir() else "ORPHANED")
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        entries.append((d.name, root, state, size))
    if args.repos_cmd in (None, "list"):
        for name, root, state, size in entries:
            print(f"{state:9} {size / 1_048_576:8.1f}MB  {name}  ->  {root or '?'}")
        return
    # prune
    victims = [e for e in entries if e[2] == "ORPHANED"]
    if args.unknown:
        victims += [e for e in entries if e[2] == "unknown"]
    if not victims:
        print("nothing to prune")
        return
    import shutil as _shutil

    for name, _root, _state, _size in victims:
        _shutil.rmtree(repos / name)
        print(f"pruned {name}")


# ------------------------------------------------------------------ commands

AGENTS_SECTION = """<!-- atlasix:begin (managed) -->
## atlasix — structural index of this repo

This repo is indexed with [atlasix](https://github.com/acidsugarx/atlasix).
Working rules for any agent:

- BEFORE changing anything: `atlasix build` (if index is stale), then
  `atlasix who-uses <symbol>` / `atlasix show <path> --resolved` /
  `atlasix search "<intent>"` / `atlasix duplicates` to understand impact.
- AFTER refactoring: `atlasix build && atlasix lint` is the acceptance check —
  duplication warnings must not grow; unresolved-ref findings must not appear.
- Full docs & pack authoring: `skill://atlasix` (or the AGENT.md at the URL above).
<!-- atlasix:end -->
"""


def _embed_agents_md(root: Path):
    f = root / "AGENTS.md"
    section = AGENTS_SECTION
    if f.exists():
        body = f.read_text(encoding="utf-8")
        if "atlasix:begin" in body:
            import re as _re

            body = _re.sub(r"<!-- atlasix:begin.*?atlasix:end -->\n?", section, body, flags=_re.S)
            f.write_text(body, encoding="utf-8")
            print(f"updated atlasix section in {f}")
            return
        f.write_text(body.rstrip("\n") + "\n\n" + section, encoding="utf-8")
    else:
        f.write_text(section, encoding="utf-8")
    print(f"wrote atlasix section to {f} — commit it so every agent sees these rules")


def cmd_init(args):
    root = _root()
    if args.local:
        d = root / ".atlas"
    else:
        d = _atlas_dir(root, create=True)
    (d / "rules").mkdir(parents=True, exist_ok=True)
    pack = d / "pack.yaml"
    if not pack.exists():
        pack.write_text(DEFAULT_PACK, encoding="utf-8")
        print(f"wrote {pack} (empty pack)")
    if d == root / ".atlas":
        (d / ".gitignore").write_text("index.db\nindex.db-wal\nindex.db-shm\n", encoding="utf-8")
    if not args.no_agents_md:
        _embed_agents_md(root)
    print(f"initialized {d}")


def cmd_build(args):
    root = _root()
    d = _atlas_dir(root, create=True)
    pk = _load_pack(root)
    ix = Indexer(root, pk, with_vectors=not args.no_vectors, atlas_dir=d)
    stats = ix.build()
    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        for k, v in stats.items():
            print(f"{k}: {v}")


def cmd_profile(args):
    root = _root()
    conn = _open_db(root)
    from collections import Counter

    exts = Counter()
    dirs = Counter()
    for r in conn.execute("SELECT path FROM files"):
        p = r["path"]
        ext = p.rsplit(".", 1)[-1] if "." in p.split("/")[-1] else "(none)"
        exts[ext] += 1
        dirs["/".join(p.split("/")[:-1]) or "."] += 1
    print("== extensions ==")
    for e, c in exts.most_common(10):
        print(f"  {e}: {c}")
    print("== top dirs (files) ==")
    for d, c in dirs.most_common(15):
        print(f"  {d}: {c}")
    print("== entity kinds ==")
    for r in conn.execute("SELECT kind,count(*) c FROM entities GROUP BY kind ORDER BY c DESC"):
        print(f"  {r['kind']}: {r['c']}")
    print("== top field values ==")
    for r in conn.execute(
        "SELECT kind, name, count(*) c FROM entities WHERE kind!='file' GROUP BY kind,name ORDER BY c DESC LIMIT 10"
    ):
        print(f"  {r['kind']}:{r['name']} x{r['c']}")
    n = conn.execute("SELECT count(*) FROM edges WHERE dst_id IS NULL").fetchone()[0]
    print(f"== unresolved refs: {n}")
    for r in conn.execute(
        "SELECT e.path, e.name, ed.rel, ed.raw_ref FROM edges ed JOIN entities e ON e.id=ed.src_id "
        "WHERE ed.dst_id IS NULL LIMIT 15"
    ):
        print(f"  {r['path']}:{r['name']} --{r['rel']}--> {r['raw_ref']}")
    print("== duplicate clusters ==")
    clusters = conn.execute("SELECT key,value FROM meta WHERE key LIKE 'cluster:%' ORDER BY key").fetchall()
    for row in clusters[:10]:
        ids = json.loads(row["value"])
        items = conn.execute(
            f"SELECT e.kind,e.name,e.path,e.line FROM text_units tu JOIN entities e ON e.id=tu.entity_id "
            f"WHERE tu.id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
        print(f"  {row['key']} ({len(ids)} units):")
        for it in items:
            print(f"    {it['kind']}:{it['name']} ({it['path']}:{it['line']})")
    if len(clusters) > 10:
        print(f"  ... {len(clusters) - 10} more")


def _find_entities(conn, needle: str):
    rows = conn.execute(
        "SELECT id,kind,path,name,line FROM entities WHERE name=? OR path=? OR name LIKE ? LIMIT 50",
        (needle, needle, f"%{needle}%"),
    ).fetchall()
    return rows


def cmd_show(args):
    conn = _open_db(_root())
    p = args.path
    rows = conn.execute("SELECT id,kind,name,line,data_json FROM entities WHERE path=? ORDER BY line", (p,)).fetchall()
    if not rows:
        print(f"no entities for {p}", file=sys.stderr)
        sys.exit(1)
    for r in rows:
        data = json.loads(r["data_json"])
        extra = ""
        if data:
            keys = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in list(data.items())[:6])
            extra = f"  [{keys}]"
        print(f"{r['kind']}:{r['name']}  {p}:{r['line'] or 0}{extra}")
        if args.resolved:
            for e in conn.execute(
                "SELECT rel,dst_id,raw_ref FROM edges WHERE src_id=?", (r["id"],)
            ):
                if e["dst_id"] is not None:
                    t = conn.execute("SELECT kind,path,name,line FROM entities WHERE id=?", (e["dst_id"],)).fetchone()
                    print(f"    -{e['rel']}-> {t['kind']}:{t['name']} ({t['path']}:{t['line'] or 0})")
                else:
                    print(f"    -{e['rel']}-> UNRESOLVED {e['raw_ref']!r}")


def cmd_who_uses(args):
    conn = _open_db(_root())
    targets = _find_entities(conn, args.target)
    if not targets:
        print(f"no entity matches {args.target!r}", file=sys.stderr)
        sys.exit(1)
    seen_edges = set()
    frontier = [t["id"] for t in targets]
    for hop in (1, 2):
        nxt = []
        for tid in frontier:
            for e in conn.execute(
                "SELECT src_id,rel,raw_ref,dst_id FROM edges WHERE dst_id=?", (tid,)
            ):
                key = (e["src_id"], tid, e["rel"])
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                s = conn.execute("SELECT kind,path,name,line FROM entities WHERE id=?", (e["src_id"],)).fetchone()
                print(f"hop{hop}: {s['kind']}:{s['name']} ({s['path']}:{s['line'] or 0}) --{e['rel']}--> {args.target if hop==1 else '...'}")
                nxt.append(e["src_id"])
            # raw refs pointing at this name (never resolved)
            for e in conn.execute(
                "SELECT src_id,rel FROM edges WHERE dst_id IS NULL AND raw_ref LIKE ?", (f"%{args.target}%",)
            ):
                s = conn.execute("SELECT kind,path,name FROM entities WHERE id=?", (e["src_id"],)).fetchone()
                print(f"hop{hop}(raw): {s['kind']}:{s['name']} ({s['path']}) --{e['rel']}--> ~{args.target}")
        frontier = nxt


_RU_SUFFIXES = sorted(
    ["иями", "ями", "ами", "иями", "ов", "ев", "ий", "ых", "их", "ах", "ях", "ой", "ей",
     "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ам", "ям", "ую", "юю", "ем", "ом",
     "ить", "ать", "ять", "еть", "уть", "ешь", "ишь", "ают", "яют", "ует", "ишь",
     "у", "ю", "а", "я", "ы", "и", "е", "ь", "о"],
    key=len, reverse=True,
)


def _tokenize(text: str) -> list:
    """Lowercase tokens with a light RU stemmer (Porter-lite suffix stripping)."""
    out = []
    for tok in text.lower().split():
        t = tok.strip(".,:;\"'()[]{}<>=|`!?/*")
        if not t:
            continue
        if len(t) > 5 and "а" <= t[-1] <= "я":
            for suf in _RU_SUFFIXES:
                if len(t) - len(suf) >= 5 and t.endswith(suf):
                    t = t[: -len(suf)]
                    break
            if len(t) >= 6 and (t[-1] in "аеёиоуыэюяй"):
                t = t[:-1]  # unify trunk: уведомлени/уведомлен, депло/деплой
        out.append(t)
    return out


def cmd_search(args):
    conn = _open_db(_root())
    rows = conn.execute("SELECT id,ref_table,ref_id,text,vec FROM chunks").fetchall()
    if not rows:
        print("index empty — run `atlasix build`", file=sys.stderr)
        sys.exit(1)
    texts = [r["text"] for r in rows]
    scores: dict[int, float] = {}
    # BM25
    try:
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi([_tokenize(t) for t in texts])
        bm = bm25.get_scores(_tokenize(args.text))
        order = [i for i in range(len(rows)) if bm[i] > 0]
        order.sort(key=lambda i: -bm[i])
        order = order[: 5 * args.top]
        rrf = {rows[i]["id"]: 0.0 for i in order}
        for rank, i in enumerate(order):
            boost = 1.25 if rows[i]["ref_table"] == "entities" else 1.0
            rrf[rows[i]["id"]] += boost / (60 + rank + 1)
    except Exception:
        rrf = {}
    ref_kind = {r["id"]: r["ref_table"] for r in rows}
    # vectors
    if schema.VEC_OK and rows and rows[0]["vec"] is not None:
        try:
            import numpy as np
            from fastembed import TextEmbedding

            from . import config

            name, cache_dir = config.embedding_model()
            enc = TextEmbedding(name, cache_dir=cache_dir) if cache_dir else TextEmbedding(name)
            qv = list(enc.embed([args.text]))[0]
            qv = np.array(qv if not hasattr(qv, "tolist") else qv)
            mats, ids = [], []
            for r in rows:
                if r["vec"]:
                    ids.append(r["id"])
                    mats.append(np.frombuffer(r["vec"], dtype=np.float32))
            M = np.stack(mats)
            M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
            qn = qv / (np.linalg.norm(qv) + 1e-9)
            sims = M @ qn
            order = np.argsort(-sims)[: 5 * args.top]
            for rank, i in enumerate(order):
                boost = 1.25 if ref_kind.get(ids[i]) == "entities" else 1.0
                rrf[ids[i]] = rrf.get(ids[i], 0.0) + boost / (60 + rank + 1)
        except Exception:
            pass
    top = sorted(rrf.items(), key=lambda kv: -kv[1])[: args.top]
    for cid, sc in top:
        r = conn.execute("SELECT ref_table,ref_id,text FROM chunks WHERE id=?", (cid,)).fetchone()
        if r["ref_table"] == "entities":
            e = conn.execute("SELECT kind,path,name,line FROM entities WHERE id=?", (r["ref_id"],)).fetchone()
            loc = f"{e['kind']}:{e['name']} {e['path']}:{e['line'] or 0}"
        else:
            tu = conn.execute("SELECT path,line FROM text_units WHERE id=?", (r["ref_id"],)).fetchone()
            loc = f"text_unit {tu['path']}:{tu['line'] or 0}"
        snippet = r["text"].splitlines()[0][:100]
        print(f"{sc:.4f}  {loc}\n        {snippet}")


def cmd_graph(args):
    conn = _open_db(_root())
    g = gmod.build_graph(conn)
    if args.cycles:
        found = gmod.cycles(g)
        if not found:
            print("no cycles")
            return
        for c in found:
            print(" -> ".join(gmod.label(g, n) for n in c))
        return
    if args.path_between:
        a, b = args.path_between
        ea = _find_entities(conn, a)
        eb = _find_entities(conn, b)
        if not ea or not eb:
            print("entity not found", file=sys.stderr)
            sys.exit(1)
        p = gmod.shortest_path(g, ea[0]["id"], eb[0]["id"])
        rev = False
        if not p:
            p = gmod.shortest_path(g.reverse(copy=False), ea[0]["id"], eb[0]["id"])
            rev = True
        if not p:
            print("no path")
        else:
            arrow = " <-" if rev else " -> "
            print(arrow.join(gmod.label(g, n) for n in p))
        return
    targets = _find_entities(conn, args.target)
    if not targets:
        print(f"no entity matches {args.target!r}", file=sys.stderr)
        sys.exit(1)
    sub = gmod.subgraph(g, targets[0]["id"], args.depth)
    if args.dot:
        print(gmod.to_dot(sub))
    else:
        for n in sub.nodes:
            marker = " *" if n == targets[0]["id"] else ""
            print(gmod.label(g, n) + marker)
        for u, v, d in sub.edges(data=True):
            print(f"{gmod.label(g, u)} --{d['rel']}--> {gmod.label(g, v)}")


def cmd_duplicates(args):
    conn = _open_db(_root())
    clusters = conn.execute("SELECT key,value FROM meta WHERE key LIKE 'cluster:%' ORDER BY key").fetchall()
    if not clusters:
        print("no duplicate clusters")
        return
    for row in clusters:
        ids = json.loads(row["value"])
        items = conn.execute(
            f"SELECT e.kind,e.name,e.path,e.line,t.normalized_hash FROM text_units t "
            f"JOIN entities e ON e.id=t.entity_id WHERE t.id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
        print(f"{row['key']} ({len(ids)} units):")
        for it in items:
            print(f"  {it['kind']}:{it['name']} ({it['path']}:{it['line'] or 0})")


def cmd_lint(args):
    root = _root()
    conn = _open_db(root)
    try:
        rules = rmod.load_rules(_atlas_dir(root) / "rules")
    except rmod.RuleError as e:
        print(f"rule error: {e}", file=sys.stderr)
        sys.exit(2)
    if not rules:
        print("no rules in .atlas/rules/")
        return
    findings = rmod.lint(conn, rules)
    errors = 0
    for f in findings:
        print(f"[{f['severity']}] {f['id']} {f['path']} {f['subject']}: {f['message']}")
        if f["severity"] == "error":
            errors += 1
    print(f"{len(findings)} findings ({errors} errors)")
    sys.exit(1 if errors else 0)


def cmd_bootstrap_hint(args):
    print(HINT)


def cmd_config(args):
    from . import config

    if args.config_cmd == "set":
        try:
            config.set_value(args.key, args.value)
        except KeyError as e:
            print(e, file=sys.stderr)
            sys.exit(2)
        shown = "********" if args.key == "hf_token" and args.value else args.value
        print(f"{args.key} = {shown}  (stored in {config.SETTINGS_PATH})")
    elif args.config_cmd == "get":
        print(config.get_effective(args.key) or "")
    else:  # list
        cfg = config.load_settings()
        for k in sorted(cfg):
            v = cfg[k]
            if k == "hf_token" and v:
                v = f"******** ({'encrypted' if v.startswith('enc:') else 'PLAINTEXT!'})"
            print(f"{k}: {v or '(default)'}")


def cmd_hook_refresh(args):
    """Debounced background index rebuild — called from harness hooks (edit tools).

    Instant: checks a 30s marker in the state dir and spawns a detached
    `atlasix build --no-vectors` only when the marker is stale. Safe to call
    on every edit.
    """
    import subprocess
    import time

    root = _root()
    d = _atlas_dir(root, create=True)
    marker = d / ".rebuild-marker"
    now = time.time()
    if marker.exists():
        try:
            if now - marker.stat().st_mtime < 30:
                return
        except OSError:
            pass
    marker.touch()
    subprocess.Popen(
        [sys.executable, "-m", "atlasix", "build", "--no-vectors", "--json"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def cmd_plugin(args):
    import json as _json

    pkg_dir = Path(__file__).parent  # wheel (force-include) layout
    repo_dir = Path(__file__).parent.parent  # repo checkout

    def asset(rel: str) -> Path:
        for base in (pkg_dir, repo_dir):
            c = base / rel
            if c.is_file():
                return c
        print(f"{rel} not found in package", file=sys.stderr)
        sys.exit(2)

    if args.harness == "opencode":
        plugins = Path.home() / ".config" / "opencode" / "plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        (plugins / "atlasix.js").write_text(asset("opencode/atlasix.js").read_text(encoding="utf-8"), encoding="utf-8")
        pkg = Path.home() / ".config" / "opencode" / "package.json"
        data = {"dependencies": {}}
        if pkg.exists():
            data = _json.loads(pkg.read_text(encoding="utf-8"))
            data.setdefault("dependencies", {})
        data["dependencies"]["@opencode-ai/plugin"] = "*"
        pkg.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"installed {plugins / 'atlasix.js'}")
        print(f"deps: {pkg} (@opencode-ai/plugin — bun install runs on startup)")
        print("restart opencode to load the plugin")

    elif args.harness == "oh-my-pi":
        hooks = Path.home() / ".omp" / "agent" / "hooks" / "pre"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "atlasix.ts").write_text(asset("oh-my-pi/atlasix.ts").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"installed {hooks / 'atlasix.ts'}")
        print("it calls `atlasix hook refresh` (debounced background rebuild) after edit-tool results")
        print("restart oh-my-pi to load the hook")

    elif args.harness == "claude-code":
        cmds = Path.home() / ".claude" / "commands"
        cmds.mkdir(parents=True, exist_ok=True)
        for name in ("atlasix.md", "atlasix-lint.md"):
            (cmds / name).write_text(asset(f"claude-code/commands/{name}").read_text(encoding="utf-8"), encoding="utf-8")
            print(f"installed {cmds / name}")
        settings = Path.home() / ".claude" / "settings.json"
        data = {}
        if settings.exists():
            data = _json.loads(settings.read_text(encoding="utf-8"))
            settings.with_suffix(".json.bak-atlasix").write_text(settings.read_text(encoding="utf-8"), encoding="utf-8")
        hooks_cfg = data.setdefault("hooks", {})
        post = [h for h in hooks_cfg.get("PostToolUse", []) if "atlasix" not in _json.dumps(h)]
        post.append(
            {
                "matcher": "Edit|Write|MultiEdit|NotebookEdit",
                "hooks": [{"type": "command", "command": "atlasix hook refresh"}],
            }
        )
        hooks_cfg["PostToolUse"] = post
        settings.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"hooks: PostToolUse → `atlasix hook refresh` added to {settings} (backup .bak-atlasix)")
        print("slash commands: /atlasix <symbol|intent>, /atlasix-lint")
    else:
        print(f"unknown harness {args.harness!r}; available: opencode, oh-my-pi, claude-code", file=sys.stderr)
        sys.exit(2)



def cmd_pack(args):
    from . import packs

    if args.pack_cmd == "list":
        found = packs.available()
        if not found:
            print("no built-in packs")
        for p in found:
            print(f"{p['name']}: {p['description'] or '(no description)'}")
        return
    # import
    root = _root()
    try:
        written = packs.install(args.name, _atlas_dir(root, create=True), force=args.force, frm=getattr(args, "from", None))
    except KeyError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except FileExistsError as e:
        print(f"{e}", file=sys.stderr)
        sys.exit(3)
    for f in written:
        print(f"installed {f}")
    print("next: atlasix build && atlasix lint")


def cmd_mcp(args):
    from .mcp_server import serve

    serve()


def cmd_update(args):
    import os
    import subprocess
    import shutil

    uv = shutil.which("uv")
    if uv is None:
        print("uv not found — install: https://docs.astral.sh/uv/", file=sys.stderr)
        sys.exit(2)
    src = os.environ.get("ATLASIX_REPO", "https://github.com/acidsugarx/atlasix")
    src = src if os.path.isdir(src) else f"git+{src}"
    from . import __version__

    print(f"atlasix {__version__} → updating from {src}")
    r = subprocess.run([uv, "tool", "install", "--force", src])
    if r.returncode != 0:
        sys.exit(r.returncode)
    subprocess.run([sys.executable, "-c", "from atlasix import __version__; print('now:', __version__)"])


SKILL_ROOTS = [
    # (harness, skills root) — oh-my-pi/pi canonical `agents` provider is the default
    ("oh-my-pi / pi (agents)", Path.home() / ".agents" / "skills"),
    ("Claude Code", Path.home() / ".claude" / "skills"),
    ("opencode", Path.home() / ".config" / "opencode" / "skill"),
    ("codex", Path.home() / ".codex" / "skills"),
]


def cmd_skill(args):
    from pathlib import Path as _P

    candidates = [
        _P(__file__).parent / "SKILL.md",           # wheel (force-include)
        _P(__file__).parent.parent / "SKILL.md",    # repo checkout (editable)
    ]
    src = next((c for c in candidates if c.is_file()), None)
    if src is None:
        print("SKILL.md not found in package", file=sys.stderr)
        sys.exit(2)
    body = src.read_text(encoding="utf-8")
    guide = src.parent / "packs-authoring.md" if src.parent.name == "atlasix" else src.with_name("packs-authoring.md")
    if args.dir:
        roots = [("custom", Path(args.dir))]
    else:
        detected = [(h, r) for h, r in SKILL_ROOTS if r.parent.exists()]
        roots = detected or [SKILL_ROOTS[0]]
    for harness, root in roots:
        d = root / "atlasix"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        if guide.is_file():
            (d / "packs-authoring.md").write_text(guide.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"installed skill for {harness}: {d / 'SKILL.md'}")
    if not args.dir and len(roots) == 1:
        print("note: only default root used; other harnesses not detected (pass --dir to force)")

def main(argv=None):
    try:
        import signal

        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass  # windows
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass  # stream without reconfigure support
    ap = argparse.ArgumentParser(prog="atlasix", description="structural repo index for LLM agents")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__import__('atlasix').__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init")
    sub.add_parser("update").set_defaults(fn=cmd_update)
    hk = sub.add_parser("hook")
    hksub = hk.add_subparsers(dest="hook_cmd", required=True)
    hksub.add_parser("refresh").set_defaults(fn=cmd_hook_refresh)
    pl = sub.add_parser("plugin")
    pl.add_argument("harness", choices=["opencode", "oh-my-pi", "claude-code"])
    pl.set_defaults(fn=cmd_plugin)
    ip.add_argument("--local", action="store_true", help="create .atlas/ inside the repo instead of ~/.atlasix/repos/")
    ip.add_argument("--no-agents-md", action="store_true", help="skip embedding atlasix rules into repo AGENTS.md")
    ip.set_defaults(fn=cmd_init)
    b = sub.add_parser("build")
    b.add_argument("--no-vectors", action="store_true")
    b.add_argument("--json", action="store_true")
    b.set_defaults(fn=cmd_build)
    rs = sub.add_parser("repos")
    rss = rs.add_subparsers(dest="repos_cmd")
    mp = sub.add_parser("mcp")
    mp.set_defaults(fn=cmd_mcp)
    sk = sub.add_parser("skill")
    sksub = sk.add_subparsers(dest="skill_cmd", required=True)
    ski = sksub.add_parser("install")
    ski.add_argument("--dir", help="custom skills root (default: autodetect harness roots)")
    sk.set_defaults(fn=cmd_skill, skill_cmd="install")
    rss.add_parser("list")
    rp = rss.add_parser("prune")
    rp.add_argument("--unknown", action="store_true", help="also prune state dirs without a root marker")
    rs.set_defaults(fn=cmd_repos, repos_cmd=None)
    pk = sub.add_parser("pack")
    psub = pk.add_subparsers(dest="pack_cmd", required=True)
    psub.add_parser("list")
    pi = psub.add_parser("import")
    pi.add_argument("name")
    pi.add_argument("--from", help="pack source: local dir or git URL (private packs); default: built-in registry")
    pi.add_argument("--force", action="store_true")
    pk.set_defaults(fn=cmd_pack, pack_cmd="list")
    c = sub.add_parser("config")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    cs = csub.add_parser("set")
    cs.add_argument("key")
    cs.add_argument("value")
    cg = csub.add_parser("get")
    cg.add_argument("key")
    csub.add_parser("list")
    c.set_defaults(fn=cmd_config, config_cmd="list")
    sub.add_parser("profile").set_defaults(fn=cmd_profile)
    s = sub.add_parser("show")
    s.add_argument("path")
    s.add_argument("--resolved", action="store_true")
    s.set_defaults(fn=cmd_show)
    w = sub.add_parser("who-uses")
    w.add_argument("target")
    w.set_defaults(fn=cmd_who_uses)
    sr = sub.add_parser("search")
    sr.add_argument("text")
    sr.add_argument("--top", type=int, default=5)
    sr.set_defaults(fn=cmd_search)
    g = sub.add_parser("graph")
    g.add_argument("target", nargs="?", default=None)
    g.add_argument("--depth", type=int, default=1)
    g.add_argument("--dot", action="store_true")
    g.add_argument("--cycles", action="store_true")
    g.add_argument("--path-between", nargs=2, metavar=("A", "B"))
    g.set_defaults(fn=cmd_graph)
    sub.add_parser("duplicates").set_defaults(fn=cmd_duplicates)
    sub.add_parser("lint").set_defaults(fn=cmd_lint)
    sub.add_parser("bootstrap-hint").set_defaults(fn=cmd_bootstrap_hint)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
