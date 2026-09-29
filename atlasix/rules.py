"""Rule engine: declarative YAML rules over the fact model, AST-whitelisted `where` eval."""
from __future__ import annotations

import ast
import fnmatch
import json
import sqlite3
from pathlib import Path

from ruamel.yaml import YAML

ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
    ast.Compare, ast.Eq, ast.NotEq, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.Gt, ast.GtE, ast.Lt, ast.LtE,
    ast.Attribute, ast.Name, ast.Load, ast.Constant,
    ast.Call, ast.Subscript, ast.Index, ast.Slice,
    ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.comprehension, ast.Store,
    ast.BinOp, ast.USub, ast.UAdd,
)
ALLOWED_NAMES = {"fact", "index", "len", "str", "int", "float", "bool", "set", "list"}


class RuleError(Exception):
    pass


class Rule:
    def __init__(self, cfg: dict, src: Path):
        self.src = src
        self.id = cfg.get("id")
        self.severity = cfg.get("severity", "warn")
        if not self.id:
            raise RuleError(f"{src}: rule needs 'id'")
        if self.severity not in ("error", "warn", "info"):
            raise RuleError(f"{src}: bad severity {self.severity!r}")
        m = cfg.get("match") or {}
        self.kind = m.get("kind")
        self.path_glob = m.get("path_glob")
        self.where_src = cfg.get("where")
        self.message = cfg.get("message", self.id)
        self.where = None
        if self.where_src:
            try:
                self.where = compile(self.where_src, f"<rule {self.id}>", "eval", ast.PyCF_ONLY_AST)
            except SyntaxError as e:
                raise RuleError(f"{src}: where syntax error: {e}") from None
            _validate(self.where, src)

    def matches(self, fact: dict) -> bool:
        if self.kind and fact["kind"] != self.kind:
            return False
        if self.path_glob and not fnmatch.fnmatch(fact["path"], self.path_glob) \
                and not fnmatch.fnmatch(fact["path"], "*/" + self.path_glob):
            return False
        if self.where:
            return bool(_eval(self.where, fact))
        return True


def _validate(tree: ast.AST, src: Path):
    local_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    local_names.add(t.id)
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise RuleError(f"{src}: disallowed syntax in where: {type(node).__name__} ({ast.dump(node)[:80]})")
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in ALLOWED_NAMES and node.id not in local_names:
                raise RuleError(f"{src}: disallowed name in where: {node.id!r}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise RuleError(f"{src}: disallowed attribute in where: {node.attr!r}")


class _Fact(dict):
    """fact.name / fact.path / fact.kind / fact.data.get(...)"""
    @property
    def id(self):
        return self["id"]

    @property
    def kind(self):
        return self["kind"]

    @property
    def path(self):
        return self["path"]

    @property
    def name(self):
        return self["name"]

    @property
    def line(self):
        return self["line"]

    @property
    def data(self):
        return self["_data"]


class _IndexView:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._dup_hashes = None
        self._dup_entities = None

    @property
    def duplicate_hashes(self) -> set:
        if self._dup_hashes is None:
            ids = sorted(self.duplicate_entity_ids)
            if ids:
                q = f"SELECT DISTINCT normalized_hash FROM text_units WHERE entity_id IN ({','.join('?' * len(ids))})"
                self._dup_hashes = {r[0] for r in self._conn.execute(q, ids)}
            else:
                self._dup_hashes = set()

    def has_unresolved_edge(self, entity_id: int, rel: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM edges WHERE src_id=? AND rel=? AND dst_id IS NULL", (entity_id, rel)
        ).fetchone() is not None
    @property
    def duplicate_entity_ids(self) -> set:
        if self._dup_entities is None:
            row = self._conn.execute("SELECT value FROM meta WHERE key='dup_entity_ids'").fetchone()
            self._dup_entities = set(json.loads(row[0])) if row else set()
        return self._dup_entities

    def in_duplicate_cluster(self, entity_id: int) -> bool:
        return entity_id in self.duplicate_entity_ids

    @property
    def unresolved_refs(self) -> list:
        return [
            dict(r) for r in self._conn.execute(
                "SELECT e.path, e.name, ed.rel, ed.raw_ref FROM edges ed JOIN entities e ON e.id=ed.src_id "
                "WHERE ed.dst_id IS NULL"
            )
        ]


def _eval(tree: ast.AST, fact: dict):
    env = {"__builtins__": {}, "len": len, "str": str, "int": int, "float": float,
           "bool": bool, "set": set, "list": list}
    env.update({"fact": fact, "index": fact["_index"]})
    return eval(compile(tree, "<where>", "eval"), env)


def load_rules(rules_dir: Path) -> list[Rule]:
    rules = []
    if not rules_dir.is_dir():
        return rules
    yaml = YAML(typ="safe")
    for f in sorted(rules_dir.glob("*.yaml")) + sorted(rules_dir.glob("*.yml")):
        cfg = yaml.load(f.read_text(encoding="utf-8")) or {}
        if isinstance(cfg, list):
            for item in cfg:
                rules.append(Rule(item, f))
        else:
            rules.append(Rule(cfg, f))
    return rules


def lint(conn: sqlite3.Connection, rules: list[Rule]) -> list[dict]:
    view = _IndexView(conn)
    findings = []
    for row in conn.execute("SELECT id,kind,path,name,line,data_json FROM entities"):
        data = json.loads(row["data_json"])
        fact = _Fact(id=row["id"], kind=row["kind"], path=row["path"], name=row["name"],
                     line=row["line"] or 0, _data=data, _index=view)
        for rule in rules:
            try:
                hit = rule.matches(fact)
            except Exception:
                continue
            if hit:
                findings.append({
                    "id": rule.id,
                    "severity": rule.severity,
                    "path": row["path"],
                    "subject": f"{row['kind']}:{row['name']}",
                    "message": _fmt(rule.message, fact, view),
                })
    return findings


def _fmt(message: str, fact: dict, view: _IndexView) -> str:
    kwargs = dict(fact)
    kwargs.pop("_data", None), kwargs.pop("_index", None)
    kwargs["cluster_id"] = "dup"
    try:
        return message.format(**kwargs)
    except (KeyError, IndexError):
        return message
