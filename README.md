# atlasix

Agent-agnostic structural repo indexer for LLM agents (oh-my-pi, Claude Code,
Cursor, anything with a shell): navigation, who-uses, duplicate clusters,
declarative lint, hybrid semantic search, graph analysis.

- **Domain-free core** — all domain knowledge lives in the analyzed repo's
  committed `.atlas/pack.yaml` (+ `.atlas/rules/*.yaml`), authored by an LLM
  via `atlasix bootstrap-hint`.
- **Deterministic facts**: `entities / edges / text_units / chunks` in
  `.atlas/index.db` (SQLite + sqlite-vec).
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
$ atlasix lint
```

Agent workflow: see `SKILL.md`. Architecture rules: see `AGENTS.md`.
