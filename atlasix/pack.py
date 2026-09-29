"""Declarative domain packs: load/validate .atlas/pack.yaml, extractors, resolvers."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from ruamel.yaml import YAML

EXTRACTORS = {"yaml_jobs", "yaml_keys", "regex", "json_pointer", "line_symbols", "tree_sitter"}
RESOLVERS = {"same_doc_dict", "repo_path", "entity_name", "global_name", "include_aware", "same_doc_anchor", "none"}


class PackError(Exception):
    pass


class EntitySpec:
    KEYS = {"files", "extractor", "fields", "pattern", "pointer", "language", "symbol_types"}

    def __init__(self, kind, cfg):
        if not isinstance(cfg, dict):
            raise PackError(f"entity_types.{kind}: expected mapping")
        unknown = set(cfg) - self.KEYS
        if unknown:
            raise PackError(f"entity_types.{kind}: unknown key(s) {sorted(unknown)}; allowed: {sorted(self.KEYS)}")
        self.kind = kind
        self.files = cfg.get("files")
        if not self.files or not isinstance(self.files, list):
            raise PackError(f"entity_types.{kind}: 'files' list required")
        self.extractor = cfg.get("extractor")
        if self.extractor not in EXTRACTORS:
            raise PackError(
                f"entity_types.{kind}: unknown extractor {self.extractor!r}; "
                f"available: {sorted(EXTRACTORS)}"
            )
        self.fields = cfg.get("fields", []) or []
        self.pattern = cfg.get("pattern")
        self.json_pointer = cfg.get("pointer")
        if self.extractor == "tree_sitter":
            self.language = cfg.get("language", "")
            self.symbol_types = cfg.get("symbol_types", []) or []
        if self.extractor in ("regex", "line_symbols") and not self.pattern:
            raise PackError(f"entity_types.{kind}: extractor {self.extractor} needs 'pattern'")
        if self.extractor == "json_pointer" and not self.json_pointer:
            raise PackError(f"entity_types.{kind}: json_pointer needs 'pointer'")


class RefSpec:
    KEYS = {"from", "resolve", "to"}

    def __init__(self, rel, cfg):
        self.rel = rel
        if not isinstance(cfg, dict):
            raise PackError(f"references.{rel}: expected mapping")
        unknown = set(cfg) - self.KEYS
        if unknown:
            raise PackError(f"references.{rel}: unknown key(s) {sorted(unknown)}; allowed: {sorted(self.KEYS)}")
        frm = cfg.get("from") or {}
        if isinstance(frm, dict):
            bad = set(frm) - {"entity", "field", "pattern"}
            if bad:
                raise PackError(f"references.{rel}.from: unknown key(s) {sorted(bad)}; allowed: ['entity', 'field', 'pattern']")
        self.entity = frm.get("entity")
        self.field = frm.get("field")
        self.pattern = frm.get("pattern")
        if not self.entity or not self.field:
            raise PackError(f"references.{rel}: 'from.entity' and 'from.field' required")
        if self.field == "raw_regex" and not self.pattern:
            raise PackError(f"references.{rel}: field raw_regex needs 'pattern'")
        self.to = cfg.get("to")
        self.resolve = cfg.get("resolve", "none")
        if self.resolve not in RESOLVERS:
            raise PackError(
                f"references.{rel}: unknown resolver {self.resolve!r}; available: {sorted(RESOLVERS)}"
            )
        if self.resolve == "entity_name" and not self.to:
            raise PackError(f"references.{rel}: resolver entity_name needs 'to' (target entity kind)")


class Pack:
    def __init__(self, data, path: Path):
        self.path = path
        self.entity_specs: dict[str, EntitySpec] = {}
        self.refs: list[RefSpec] = []
        self.text_unit = None  # (entity_kind, field)
        self.dup_min_lines = 5
        self.dup_threshold = 0.85
        self._load(data)

    ROOT_KEYS = {"version", "entity_types", "references", "text_units", "duplicates"}

    def _load(self, data):
        if not isinstance(data, dict):
            raise PackError("pack root must be a mapping")
        unknown = set(data) - self.ROOT_KEYS
        if unknown:
            raise PackError(f"{self.path}: unknown top-level key(s) {sorted(unknown)}; allowed: {sorted(self.ROOT_KEYS)}")
        version = data.get("version")
        if version != 1:
            raise PackError(f"unsupported pack version {version!r} (expected 1)")
        for kind, cfg in (data.get("entity_types") or {}).items():
            try:
                self.entity_specs[kind] = EntitySpec(kind, cfg)
            except PackError as e:
                raise PackError(f"{self.path}: {e}") from None
        for rel, cfg in (data.get("references") or {}).items():
            try:
                self.refs.append(RefSpec(rel, cfg))
            except PackError as e:
                raise PackError(f"{self.path}: {e}") from None
        tu = data.get("text_units") or {}
        if set(tu) - {"unit"}:
            raise PackError(f"{self.path}: text_units: unknown key(s) {sorted(set(tu) - {'unit'})}")
        unit = tu.get("unit") or {}
        if unit:
            if not unit.get("entity") or not unit.get("field"):
                raise PackError("text_units.unit needs 'entity' and 'field'")
            self.text_unit = (unit["entity"], unit["field"])
        dup = data.get("duplicates") or {}
        if set(dup) - {"min_lines", "threshold"}:
            raise PackError(f"{self.path}: duplicates: unknown key(s) {sorted(set(dup) - {'min_lines', 'threshold'})}")
        self.dup_min_lines = int(dup.get("min_lines", 5))
        self.dup_threshold = float(dup.get("threshold", 0.85))

    def spec_for_file(self, rel_path: str):
        """Yield EntitySpecs whose file globs match rel_path (posix form)."""
        p = rel_path.replace("\\", "/")
        for spec in self.entity_specs.values():
            for pat in spec.files:
                base = pat.replace("**/", "", 1) if pat.startswith("**/") else pat
                if (
                    fnmatch.fnmatch(p, pat)
                    or fnmatch.fnmatch(p, "*/" + pat)
                    or fnmatch.fnmatch(p, base)
                    or fnmatch.fnmatch(p, "*/" + base)
                ):
                    yield spec


def load_pack(pack_path: Path) -> Pack:
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(pack_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise PackError(f"{pack_path}: YAML parse error: {e}") from None
    return Pack(data or {}, pack_path)


# ---------------------------------------------------------------- extractors

def _lines_of(node) -> int | None:
    lc = getattr(node, "lc", None)
    try:
        return lc[0] + 1 if lc is not None else None
    except Exception:
        return None


def _extract_field(value, key):
    """Dig key from a mapping (or first list element mapping)."""
    if isinstance(value, dict):
        if key in value:
            return value[key]
    elif isinstance(value, list):
        for el in value:
            if isinstance(el, dict) and key in el:
                return el[key]
    return None


def _scalar(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


_TS_LANG_BY_EXT = {
    ".py": "python", ".rs": "rust", ".go": "go", ".js": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".jsx": "jsx", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".swift": "swift", ".scala": "scala", ".zig": "zig",
    ".lua": "lua", ".pl": "perl", ".sh": "bash", ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang", ".hs": "haskell", ".clj": "clojure", ".vim": "vim",
}


def _ts_language_for(filename: str) -> str:
    return _TS_LANG_BY_EXT.get(filename.rsplit(".", 1)[-1].lower(), "") or _TS_LANG_BY_EXT.get(
        "." + filename.rsplit(".", 1)[-1].lower(), ""
    )


def extract(spec: EntitySpec, path: Path, rel_path: str):
    """Return list of (name, line, fields_dict). May raise ParseError → skipped."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if spec.extractor in ("yaml_jobs", "yaml_keys"):
        from ruamel.yaml import YAML as _Y

        yaml = _Y(typ="rt")
        data = yaml.load(text)
        out = []
        if isinstance(data, dict):
            for name, value in data.items():
                fields = {}
                if spec.extractor == "yaml_jobs" and isinstance(value, dict):
                    ext = value.get("extends")
                    if ext is not None:
                        fields["extends"] = ext
                    anchor = getattr(value, "anchor", None)
                    if anchor is not None and getattr(anchor, "value", None):
                        fields["anchor"] = anchor.value
                    merges = getattr(value, "merge", None)
                    if merges:
                        names = []
                        for item in merges:
                            src = item[1] if isinstance(item, tuple) and len(item) > 1 else item
                            a = getattr(src, "anchor", None)
                            names.append(getattr(a, "value", None) or str(src)[:40])
                        fields["merges"] = [n for n in names if n]
                for f in spec.fields:
                    v = _extract_field(value, f)
                    if v is not None:
                        fields[f] = _scalar(v)
                ln = _lines_of(name)
                if ln is None:
                    m = re.search(rf"(?m)^{re.escape(str(name))}\s*:", text)
                    ln = text.count("\n", 0, m.start()) + 1 if m else 1
                out.append((str(name), ln, fields))
        return out
    if spec.extractor == "regex":
        rx = re.compile(spec.pattern, re.MULTILINE)
        out = []
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            groups = {str(i + 1): g for i, g in enumerate(m.groups() or ())}
            if m.groupdict():
                groups.update({k: v for k, v in m.groupdict().items() if v is not None})
            nm = next((g for g in reversed(m.groups() or ()) if g is not None), m.group(0))
            out.append((nm.strip(), line, groups))
        return out
    if spec.extractor == "line_symbols":
        rx = re.compile(spec.pattern)
        out = []
        for i, ln in enumerate(text.splitlines(), 1):
            m = rx.search(ln)
            if m:
                gd = m.groupdict()
                name = gd.get("name") or (m.group(1) if m.groups() else m.group(0))
                fields = {k: v for k, v in gd.items() if k != "name" and v is not None}
                out.append((name, i, fields))
        return out
    if spec.extractor == "tree_sitter":
        from tree_sitter_language_pack import get_parser

        lang = spec.language or _ts_language_for(path.name)
        if not lang:
            return []
        try:
            parser = get_parser(lang)
        except Exception:
            return []  # grammar not in the pack → skipped file (files.parse_status handles)
        src = text.encode("utf-8")
        tree = parser.parse(src)
        wanted = tuple(spec.symbol_types) or None
        out = []

        def visit(node, scope=None):
            name_node = node.child_by_field_name("name")
            is_symbol = (
                name_node is not None
                and node.type.endswith(("_definition", "_declaration", "_item", "_specification", "_clause"))
            )
            if is_symbol and (wanted is None or node.type in wanted):
                nm = src[name_node.start_byte : name_node.end_byte].decode("utf-8", "replace")
                line = node.start_point[0] + 1
                out.append((nm, line, {"type": node.type, **({"scope": scope} if scope else {})}))
                new_scope = nm
            else:
                new_scope = scope
            for ch in node.children:
                visit(ch, new_scope)

        visit(tree.root_node)
        return out
    if spec.extractor == "json_pointer":
        out = []
        rx = re.compile(spec.json_pointer)
        for i, ln in enumerate(text.splitlines(), 1):
            if rx.search(ln):
                out.append((ln.strip()[:120], i, {}))
        return out
    raise PackError(f"unhandled extractor {spec.extractor}")
