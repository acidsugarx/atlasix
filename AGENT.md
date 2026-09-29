# atlasix — agent self-install & usage

You are an agent (oh-my-pi, opencode, Claude Code, Cursor, or any harness with
shell access). This file is a complete, self-contained instruction: install
atlasix, index the current repository, and use it for orientation. Follow it
top to bottom. No human input required.

## 1. Install (pick exactly one path)

**Path A — persistent CLI (preferred, reusable across sessions):**

```bash
uv tool install --force git+https://github.com/acidsugarx/atlasix
```

**Path B — zero-install (nothing left on the machine):** prefix every command
below with `uvx --from git+https://github.com/acidsugarx/atlasix` instead of
`atlasix`.

Prerequisite for both: `uv`. If missing, install it first:
`curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or
`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows).

Verify: `atlasix --help` (Path A) — if it prints subcommands, continue.

**1c. Bind the repo (done by `atlasix init`):** init embeds a managed
`<!-- atlasix:begin -->` section into the repo's `AGENTS.md` — every harness
(oh-my-pi, opencode, Claude Code, …) auto-loads it in this repo, so the
rules above are enforced in all future sessions. Commit the AGENTS.md change.
Skip with `atlasix init --no-agents-md` if undesired.

**1a. Register the MCP server (if your harness supports MCP — strongest option):**

Add to the harness MCP config (user-level `mcpServers`, or project
`.mcp.json` / equivalent):

```json
{ "mcpServers": { "atlasix": { "command": "atlasix", "args": ["mcp"] } } }
```

(With Path B use command `uvx`, args `["--from",
"git+https://github.com/acidsugarx/atlasix", "atlasix", "mcp"]`.)
After registration, all atlasix commands below are available as MCP tools —
prefer them over shell invocations.

**1b. Persist yourself as a skill (do this once, right after install):**

```bash
atlasix skill install
```

This writes the atlasix SKILL.md into every detected harness skills root
(`~/.agents/skills/`, `~/.claude/skills/`, `~/.config/opencode/skill/`,
`~/.codex/skills/`), so future sessions of this harness automatically know
about atlasix instead of forgetting it. If your harness keeps skills
elsewhere, pass `--dir <skills-root>`.

## 2. Bootstrap the current repo (one-time)

```bash
atlasix init
```

- No dotfolder is created in the repo by default — state lives in
  `~/.atlasix/repos/<repo>-<hash>/` (pack, rules, index).
- If a checked-in `.atlas/` exists in the repo, it wins automatically — skip to
  step 3.

Check for a ready domain pack:

```bash
atlasix pack list          # e.g. gitlab-ci: GitLab CI monorepos
atlasix pack import gitlab-ci   # only if it fits this repo
```

Otherwise author a pack yourself:

```bash
atlasix profile                  # domain-free repo summary (your input)
atlasix bootstrap-hint           # valid pack.yaml + rules examples
# write .atlas/pack.yaml (via: atlasix init --local) or edit
# ~/.atlasix/repos/<repo>-<hash>/pack.yaml directly
```

Build and verify:

```bash
atlasix build && atlasix lint
```

`lint` warnings are findings, not errors; exit 0 is expected unless a rule with
`severity: error` fires (means the repo really has a broken reference).

## 3. Use it (before any change in this repo)

```bash
atlasix show <path> --resolved      # entities of a file, edges expanded
atlasix who-uses <name-or-path>     # reverse references, 2 hops
atlasix duplicates                  # copy-paste clusters
atlasix search "<intent>" --top 5   # hybrid BM25+vector search (RU/EN)
atlasix graph <name> --depth 2      # neighborhood; --cycles; --path-between A B
```

## 4. After refactoring

`atlasix build && atlasix lint` is the acceptance criterion: duplication
warnings should drop; unresolved-ref findings must not appear.

## 5. Config (optional)

```bash
atlasix config set hf_token hf_...     # encrypted at rest (~/.atlasix/secret.key)
atlasix config set model_dir /path/onnx  # fully offline embeddings
atlasix repos list / prune             # state-dir hygiene
```

## Rules for you, the agent

- Never edit `~/.atlasix/` internals by hand except `pack.yaml`/`rules/`.
- Full rebuild on every `atlasix build` is by design; do not try to cache.
- If a pack for this domain doesn't exist and you author a good one, suggest
  the human upstream it into `atlasix/packs/` (one directory, one pack.yaml).
