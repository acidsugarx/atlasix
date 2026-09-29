# atlasix — structural repo index for LLM agents

atlasix gives any shell-capable agent (oh-my-pi, Claude Code, Cursor, …) instant
orientation in a structured repository: entity graph, reverse references
(who-uses), duplicate detection, declarative lint rules, semantic search and
graph analysis. The core knows nothing about your domain — all domain knowledge
lives in a committed `.atlas/pack.yaml` plus `.atlas/rules/*.yaml`.

## When to use

### Repo has no `.atlas/` directory (bootstrap)

1. Check `atlasix pack list` — a built-in pack may already fit (e.g.
   `atlasix pack import gitlab-ci` for GitLab CI monorepos). Otherwise run
   `atlasix init`, then `atlasix build` with the empty pack, then
   `atlasix profile`. Profile output (dir tree, extensions, unresolved refs,
   duplicate clusters) is domain-free and is your input for authoring a pack.
2. Read `atlasix bootstrap-hint` — it prints valid pack + rules examples.
3. Author `.atlas/pack.yaml`: map file globs to entity types via built-in
   extractors (`yaml_jobs`, `yaml_keys`, `regex`, `json_pointer`,
   `line_symbols`), declare references (extends/includes/…) with resolvers
   (`same_doc_dict`, `repo_path`, `none`), pick the text unit for duplicate
   detection (e.g. `job.script`).
4. Author `.atlas/rules/*.yaml` if the user wants lint checks.
5. `atlasix build && atlasix lint` — verify.

### Repo has `.atlas/` (normal workflow)

Before touching code, run:

- `atlasix build` (idempotent full rebuild)
- `atlasix show <path> [--resolved]` — entities of a file, edges expanded
- `atlasix who-uses <name-or-path>` — reverse references, 2 hops
- `atlasix duplicates` — copy-paste clusters (normalize `$VAR`)
- `atlasix search <text> [--top N]` — hybrid BM25+vector search
- `atlasix graph <name> [--depth N] [--dot]`, `atlasix graph --cycles`,
  `atlasix graph --path-between A B`

After a refactor, `atlasix lint` is the acceptance criterion: duplication
warnings should drop, unresolved refs must stay at zero (exit 0).

## Notes

- State location: repo `.atlas/` if it exists, else `~/.atlasix/repos/<name>-<hash>/`
  (default — no dotfolder pollution in the repo; `atlasix init --local` forces repo-local,
  useful for committed packs). `ATLASIX_HOME` env relocates the whole `~/.atlasix` root.
  `ATLASIX_HOME` env relocates the whole `~/.atlasix` root. Rebuild is always full.
- State-dir hygiene: each central dir carries a `root.txt` marker with the repo's
  absolute path. `atlasix repos list` shows ok/ORPHANED/unknown + size;
  `atlasix repos prune` deletes dirs whose repo path no longer exists
  (`--unknown` also removes pre-marker dirs); live repos are never touched.
- Vectors (fastembed, local ONNX paraphrase-multilingual-MiniLM-L12-v2 (multilingual)) download once into the user
  cache; `atlasix build --no-vectors` skips them, search degrades to BM25.
- Rule `where` expressions are AST-whitelisted; anything unsafe fails rule
  loading with file+reason.
