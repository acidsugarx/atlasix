"""Built-in pack registry: packs shipped inside atlasix (`atlasix/packs/<name>/`)."""
from __future__ import annotations

import shutil
from pathlib import Path

PACKS_DIR = Path(__file__).parent / "packs"


def available() -> list[dict]:
    out = []
    for d in sorted(PACKS_DIR.iterdir()) if PACKS_DIR.is_dir() else []:
        pack = d / "pack.yaml"
        if not (d.is_dir() and pack.exists()):
            continue
        desc = ""
        for line in pack.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") and "Built-in atlasix pack:" in line:
                desc = line.split("Built-in atlasix pack:", 1)[1].strip().rstrip("#").strip()
                break
        out.append({"name": d.name, "description": desc, "path": d})
    return out


def resolve_source(name: str, frm: str | None = None) -> Path:
    """Pack source dir: built-in registry, local dir, or git URL (cloned to temp)."""
    if not frm:
        return get(name)
    import subprocess
    import tempfile

    p = Path(frm)
    if p.is_dir():
        cand = p if (p / "pack.yaml").is_file() else p / name
        if cand.is_dir() and (cand / "pack.yaml").is_file():
            return cand
        raise KeyError(f"no pack.yaml in {frm} (looked at {cand})")
    if frm.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        tmp = Path(tempfile.mkdtemp(prefix="atlasix-pack-"))
        r = subprocess.run(["git", "clone", "--depth", "1", frm, str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            raise KeyError(f"git clone failed: {r.stderr.strip()}")
        cand = tmp if (tmp / "pack.yaml").is_file() else tmp / name
        if cand.is_dir() and (cand / "pack.yaml").is_file():
            return cand
        raise KeyError(f"repo {frm} has no pack.yaml (looked at {cand})")
    raise KeyError(f"--from must be an existing directory or a git URL, got {frm!r}")


def install(name: str, atlas_dir: Path, force: bool = False, frm: str | None = None) -> list[str]:
    """Copy pack.yaml + rules/ into the target .atlas/. Returns installed files."""
    src = resolve_source(name, frm)
    (atlas_dir / "rules").mkdir(parents=True, exist_ok=True)
    written = []
    targets = [(src / "pack.yaml", atlas_dir / "pack.yaml")]
    for rule in sorted((src / "rules").glob("*.yaml")) if (src / "rules").is_dir() else []:
        targets.append((rule, atlas_dir / "rules" / rule.name))
    from .cli import DEFAULT_PACK

    for s, t in targets:
        if t.exists() and not force:
            if t == atlas_dir / "pack.yaml" and t.read_text(encoding="utf-8") == DEFAULT_PACK:
                pass  # untouched `atlasix init` skeleton — safe to replace
            else:
                raise FileExistsError(f"refusing to overwrite {t} (use --force)")
        shutil.copyfile(s, t)
        written.append(str(t))
    return written
