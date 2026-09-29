"""Declarative domain packs: load/validate .atlas/pack.yaml, extractors, resolvers."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from ruamel.yaml import YAML

EXTRACTORS = {"yaml_jobs", "yaml_keys", "regex", "json_pointer", "line_symbols"}
RESOLVERS = {"same_doc_dict", "repo_path", "entity_name", "global_name", "none"}


class PackError(Exception):
    pass


class EntitySpec:
    def __init__(self, kind, cfg):
        if not isinstance(cfg, dict):
            raise PackError(f"entity_types.{kind}: expected mapping")
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
        if self.extractor in ("regex", "line_symbols") and not self.pattern:
            raise PackError(f"entity_types.{kind}: extractor {self.extractor} needs 'pattern'")
        if self.extractor == "json_pointer" and not self.json_pointer:
            raise PackError(f"entity_types.{kind}: json_pointer needs 'pointer'")


class RefSpec:
    def __init__(self, rel, cfg):
        self.rel = rel
        if not isinstance(cfg, dict):
            raise PackError(f"references.{rel}: expected mapping")
        frm = cfg.get("from") or {}
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

    def _load(self, data):
        if not isinstance(data, dict):
            raise PackError("pack root must be a mapping")
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
        unit = tu.get("unit") or {}
        if unit:
            if not unit.get("entity") or not unit.get("field"):
                raise PackError("text_units.unit needs 'entity' and 'field'")
            self.text_unit = (unit["entity"], unit["field"])
        dup = data.get("duplicates") or {}
        self.dup_min_lines = int(dup.get("min_lines", 5))
        self.dup_threshold = float(dup.get("threshold", 0.85))

    def spec_for_file(self, rel_path: str):
        """Yield EntitySpecs whose file globs match rel_path (posix form)."""
        p = rel_path.replace("\\", "/")
        for spec in self.entity_specs.values():
            for pat in spec.files:
                if fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p, "*/" + pat):
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
    if spec.extractor == "json_pointer":
        out = []
        rx = re.compile(spec.json_pointer)
        for i, ln in enumerate(text.splitlines(), 1):
            if rx.search(ln):
                out.append((ln.strip()[:120], i, {}))
        return out
    raise PackError(f"unhandled extractor {spec.extractor}")
