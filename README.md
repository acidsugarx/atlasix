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

## Install

```console
$ uv tool install git+https://github.com/acidsugarx/atlasix   # persistent CLI
# or zero-install one-shot:
$ uvx --from git+https://github.com/acidsugarx/atlasix atlasix init
```

Requires only `uv` (agents install it themselves if missing).

### Give it to your agent (any harness: oh-my-pi, opencode, Claude Code, Cursor)

Paste this prompt — the agent reads the instruction and does everything itself
(install, pack import, build, then uses `who-uses`/`duplicates`/`search`/`lint`):

```text
Follow https://raw.githubusercontent.com/acidsugarx/atlasix/main/AGENT.md,
register the MCP server if this harness supports MCP
({"mcpServers": {"atlasix": {"command": "atlasix", "args": ["mcp"]}}}),
run `atlasix skill install` so you permanently remember atlasix, and index
this repository. Use atlasix for orientation before any change and run
`atlasix build && atlasix lint` after refactoring as the acceptance check.
```

Shell one-liner alternative: `curl -LsSf https://raw.githubusercontent.com/acidsugarx/atlasix/main/install.sh | sh`

### MCP server (first-class tool for MCP-capable harnesses)

`atlasix mcp` runs a stdio MCP server exposing 10 tools (build, profile, show,
who_uses, search, graph, duplicates, lint, pack_import, bootstrap_hint).
Add to any MCP client config (Claude Code / opencode / oh-my-pi / Cursor):

```json
{ "mcpServers": { "atlasix": { "command": "atlasix", "args": ["mcp"] } } }
```

### OpenCode plugin

For opencode specifically there is a native plugin (5 tools + background index
refresh on edits): `atlasix plugin opencode` — copies the plugin into
`~/.config/opencode/plugins/` and pins the `@opencode-ai/plugin` dep.

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
$ atlasix repos list                    # ok / ORPHANED state dirs + size
$ atlasix repos prune                   # drop state of moved/deleted repos
```

Agent workflow: see `SKILL.md`. Architecture rules: see `AGENTS.md`.
