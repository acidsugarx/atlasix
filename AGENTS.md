# AGENTS.md

atlasix is an agent-agnostic structural repo indexer. Architecture invariants:

- **Core is domain-free.** No CI/Docker/Ansible knowledge in `atlasix/*`.
  Domain specifics live only in `.atlas/pack.yaml` + `.atlas/rules/*.yaml`
  inside the *analyzed* repo. Never add domain logic to the core.
- **Fact model is the only structure**: `files`, `entities`, `edges`,
  `text_units`, `chunks` (see `atlasix/schema.py`). New features build on it,
  not around it.
- **Deterministic parsing** (extractors in `atlasix/pack.py`, resolvers are
  pure functions). Vectors/graph are degradable layers: absence of
  `fastembed`/`sqlite-vec`/`networkx` must degrade, never crash.
- **Cross-platform**: Windows/Linux/macOS. Only `pathlib`/`tempfile` for paths;
  no posix-only calls (`fcntl`, `os.fork`, …). All deps must ship wheels.
- **Rule `where` is AST-whitelisted eval** (`atlasix/rules.py`); any new
  expression support must be added to `ALLOWED_NODES`/`ALLOWED_NAMES`
  explicitly.
- Full rebuild only, repos <100MB; no incremental indexing.

Verification: `uv run pytest`; smoke on `tests/fixtures/mini-repo`
(`atlasix init/build/profile/search`). CI runs the matrix
ubuntu/windows/macos (`.github/workflows/ci.yml`).
