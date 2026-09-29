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


def get(name: str) -> Path:
    d = PACKS_DIR / name
    if not (d.is_dir() and (d / "pack.yaml").exists()):
        raise KeyError(f"unknown pack {name!r}; available: {[p['name'] for p in available()]}")
    return d


def install(name: str, atlas_dir: Path, force: bool = False) -> list[str]:
    """Copy pack.yaml + rules/ into the target .atlas/. Returns installed files."""
    src = get(name)
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
