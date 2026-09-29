# atlasix

Agent-agnostic structural repo indexer for LLM agents (oh-my-pi, Claude Code,
Cursor, anything with a shell): navigation, who-uses, duplicate clusters,
declarative lint, hybrid semantic search, graph analysis.

- **Domain-free core** — all domain knowledge lives in the analyzed repo's
  committed `.atlas/pack.yaml` (+ `.atlas/rules/*.yaml`), authored by an LLM
  via `atlasix bootstrap-hint`.
- **Deterministic facts**: `entities / edges / text_units / chunks` in
  `.atlas/index.db` (SQLite + sqlite-vec).
- **No repo pollution by default** — pack/index/rules live in `~/.atlasix/repos/<repo>-<hash>/`;
  a committed repo `.atlas/` (e.g. via `init --local` + `pack import`) always wins.
- **Degradable layers**: no fastembed/sqlite-vec/networkx? Search falls back to
  BM25, build works, nothing crashes.
- Cross-platform: Windows/Linux/macOS, wheels only.

```console
$ atlasix init && atlasix build
$ atlasix profile          # domain-free summary → input for pack authoring
$ atlasix show lib/ansible/v2.yml --resolved
$ atlasix who-uses .ansible_deploy
$ atlasix duplicates
$ atlasix search "docker build push"
$ atlasix graph .ansible_deploy --depth 2 --dot
$ atlasix pack import gitlab-ci   # built-in domain pack + rules
$ atlasix lint
```

User state in `~/.atlasix/` (settings, model cache, encrypted HF token):

```console
$ atlasix config set hf_token hf_...   # stored encrypted (secret.key, 0600)
$ atlasix config set model_dir /path/to/local/onnx   # fully offline embeddings
$ atlasix config list
```

Agent workflow: see `SKILL.md`. Architecture rules: see `AGENTS.md`.
