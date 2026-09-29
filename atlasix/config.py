"""User-level config: ~/.atlasix/ root — settings.yaml (token encrypted), model cache."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import stat
from pathlib import Path

from ruamel.yaml import YAML

ATLASIX_HOME = Path(os.environ.get("ATLASIX_HOME", str(Path.home() / ".atlasix")))
SETTINGS_PATH = ATLASIX_HOME / "settings.yaml"
CACHE_DIR = ATLASIX_HOME / "cache"
KEY_PATH = ATLASIX_HOME / "secret.key"

DEFAULTS = {
    "model": "",        # embedding model name (empty → schema.MODEL)
    "model_dir": "",    # path to local ONNX model dir (offline)
    "cache_dir": "",    # embedding cache (empty → ~/.atlasix/cache)
    "hf_token": "",     # stored encrypted (enc:<b64>)
}


def _ensure_home():
    ATLASIX_HOME.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------- token crypto
# Stream cipher (sha256-CTR + HMAC) with a separate 0600 key file.
# Protects the settings file if it leaks; not a defense against a root/user
# compromise of the machine (use the OS keychain for that).

def _load_key() -> bytes:
    _ensure_home()
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = secrets.token_bytes(32)
    KEY_PATH.write_bytes(key)
    KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return key


def encrypt_secret(plain: str) -> str:
    key = _load_key()
    nonce = secrets.token_bytes(16)
    stream = b"".join(
        hashlib.sha256(key + nonce + i.to_bytes(4, "big")).digest()
        for i in range((len(plain) + 31) // 32)
    )[: len(plain)]
    ct = bytes(a ^ b for a, b in zip(plain.encode("utf-8"), stream))
    tag = hashlib.sha256(key + nonce + ct).digest()[:16]
    return "enc:" + base64.urlsafe_b64encode(nonce + tag + ct).decode()


def decrypt_secret(blob: str) -> str:
    raw = base64.urlsafe_b64decode(blob[4:])
    nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
    key = _load_key()
    if hashlib.sha256(key + nonce + ct).digest()[:16] != tag:
        raise ValueError("secret key mismatch — cannot decrypt hf_token (was ~/.atlasix/secret.key regenerated?)")
    stream = b"".join(
        hashlib.sha256(key + nonce + i.to_bytes(4, "big")).digest()
        for i in range((len(ct) + 31) // 32)
    )[: len(ct)]
    return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")


# ---------------------------------------------------------------- settings

def load_settings() -> dict:
    cfg = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        yaml = YAML(typ="safe")
        data = yaml.load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg


def save_settings(cfg: dict):
    _ensure_home()
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.dump(cfg, SETTINGS_PATH)
    SETTINGS_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def set_value(key: str, value: str) -> dict:
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}; available: {sorted(DEFAULTS)}")
    cfg = load_settings()
    if key == "hf_token":
        cfg[key] = encrypt_secret(value) if value else ""
    else:
        cfg[key] = value
    save_settings(cfg)
    return cfg


def get_effective(key: str) -> str:
    """settings value with env overrides for hf_token."""
    if key == "hf_token":
        for env in ("ATLASIX_HF_TOKEN", "HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
            if os.environ.get(env):
                return os.environ[env]
    cfg = load_settings()
    v = cfg.get(key, "")
    if key == "hf_token" and v.startswith("enc:"):
        v = decrypt_secret(v)
    return v


def apply_hf_env():
    token = get_effective("hf_token")
    if token:
        os.environ.setdefault("HF_TOKEN", token)
        os.environ.setdefault("HUGGINGFACEHUB_API_TOKEN", token)


def embedding_model() -> tuple[str, str | None]:
    """(model_name_or_dir, cache_dir) resolving settings."""
    from . import schema

    # fastembed/hf-hub store cache files as symlinks into blobs/; onnxruntime
 # rejects multi-file ONNX models (external *.onnx_data) resolved through them.
    _materialize_cache(CACHE_DIR if not get_effective("cache_dir") else Path(get_effective("cache_dir")))
    # multi-file ONNX models (e.g. multilingual-e5-large) break onnxruntime's
    # external-data path validation when hf-hub stores blobs as symlinks
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    model_dir = get_effective("model_dir")
    if model_dir and Path(model_dir).is_dir():
        return model_dir, None  # local ONNX dir → no cache needed
    model = get_effective("model") or schema.MODEL
    cache = get_effective("cache_dir") or str(CACHE_DIR)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return model, cache


def _materialize_cache(cache: Path):
    """Replace symlinks inside model snapshots with hardlinks (or copies) to their
    targets, so onnxruntime can load multi-file ONNX models from the snapshot dir."""
    import shutil

    models_root = cache / "models--*" if cache.is_dir() else None
    if models_root is None:
        return
    for snapshot in cache.glob("models--*/snapshots/*"):
        if not snapshot.is_dir():
            continue
        for f in snapshot.iterdir():
            if not f.is_symlink():
                continue
            target = f.resolve()
            if not target.is_file():
                continue
            tmp = f.with_name(f.name + ".atlasix-real")
            try:
                os.link(target, tmp)
            except OSError:
                shutil.copyfile(target, tmp)
            f.unlink()
            tmp.rename(f)
