# Authoring atlasix packs & rules

A pack is the ONLY place where domain knowledge lives — the core is generic.
This is a complete reference. Workflow first, syntax after.

## Workflow (LLM authoring loop)

1. `atlasix init` → `atlasix build` (empty pack) → `atlasix profile`.
   Profile gives: extensions, dir tree, entity-kind counts once you have a pack,
   unresolved refs, duplicate clusters — your evidence base.
2. Skim 3–5 representative files of each kind. Identify:
   - **entities**: named things an agent navigates (jobs, modules, endpoints, rules…)
   - **references**: how they point at each other (`extends`, `include`, imports…)
   - **text units**: the copy-paste-prone blobs worth deduplicating (scripts, bodies)
3. Write `.atlas/pack.yaml` (repo `.atlas/` via `init --local`, or central
   `~/.atlasix/repos/<repo>-<hash>/pack.yaml`). `atlasix bootstrap-hint`
   prints valid skeletons.
4. `atlasix build --no-vectors && atlasix profile` — iterate:
   - entity counts sane? (`sqlite3 <db> "select kind,count(*) from entities group by kind"`)
   - resolved edges share healthy? unresolved refs should be REAL breakage, not pattern misses
   - `atlasix who-uses <anchor>` and `atlasix show <file> --resolved` must look right
5. Add rules to `.atlas/rules/*.yaml`. `atlasix lint` — tune until signal/noise is good.
6. Ship it wherever fits:
   - **private (recommended default)**: keep the pack in your own repo/dir and
     install with `atlasix pack import <name> --from <dir-or-git-url>` —
     works for company-internal pack repos (ssh/https/file URLs; layout:
     `<repo>/<name>/{pack.yaml,rules/}` or pack at repo root);
   - **upstream**: if the domain is generic (not company-specific), contribute
     `atlasix/packs/<name>/` to the atlasix repo → `pack import <name>` for everyone.

## pack.yaml reference

```yaml
version: 1
entity_types:
  <kind>:
    files: ["glob", ...]        # fnmatch globs against repo-relative posix paths
    extractor: <name>           # see table
    fields: [k1, k2]            # keys copied into entity data (json-ized)
    pattern: <regex>            # for regex / line_symbols
    pointer: <regex>            # for json_pointer
references:
  <rel>:
    from:
      entity: <kind>            # source entity kind
      field: <field|raw_regex>  # data field name, or raw_regex = scan file text
      pattern: <regex>          # required for raw_regex; optional for data fields
    resolve: <resolver>         # see table
    to: <kind>                  # target kind (entity_name only)
text_units:
  unit: {entity: <kind>, field: <field>}   # dedup source (multiline ok)
duplicates:
  min_lines: 5                  # units shorter than this never cluster
  threshold: 0.85               # shingle-Jaccard ≥ threshold ⇒ same cluster
```

### Extractors

| name | input | yields | notes |
|---|---|---|---|
| `yaml_jobs` | YAML mapping | every top-level key | auto-adds `extends` field; best for CI/CD files |
| `yaml_keys` | YAML | every top-level key | no `extends` magic |
| `regex` | text | one entity per match | name = last non-None capture group (or full match); groups → data fields |
| `line_symbols` | text | one entity per matching line | use named group `(?P<name>...)`; other groups → fields |
| `json_pointer` | text | matching lines | line-oriented grep, fields empty |

### Resolvers

| name | behavior | unresolved when |
|---|---|---|
| `same_doc_dict` | entity with same `name` in the same file | name absent locally |
| `global_name` | edges to EVERY entity with that name (any file) | name absent anywhere — true broken ref |
| `repo_path` | file entity at that repo path (strips `./`, `/`, `path@ref`) | path not in repo |
| `entity_name` | entity of kind `to` with that name | e.g. env-var not documented |
| `none` | never resolves; raw_ref only | always (use for pure "mentions") |

Unresolved is not an error — the edge stays with `dst_id NULL` + `raw_ref`; rules
decide whether it matters.

Pattern-based references: when `pattern` is set on a data-field reference, the
field's text is scanned and each match becomes an edge (target = last group) —
that's how `uses_var: $\{?([A-Z_]+)\}?` works.

## Rules reference

`.atlas/rules/*.yaml` (list or single per file):

```yaml
id: my-rule
severity: warn          # warn | error | info (error ⇒ lint exit 1)
match:
  kind: job             # optional: entity kind
  path_glob: "a/**"     # optional: repo path glob
where: "<expression>"   # optional; must be True to fire
message: "…"            # {name} {path} {kind} {line} + any data field
```

`where` is a Python expression evaluated per entity with:

- `fact` — `.id .kind .path .name .line` and `.data.get('field', default)`
- `index` — repo-wide helpers:
  - `index.in_duplicate_cluster(fact.id)`
  - `index.duplicate_hashes`, `index.duplicate_entity_ids`
  - `index.has_unresolved_edge(fact.id, 'rel')`
  - `index.unresolved_refs` — list of dicts (`path,name,rel,raw_ref`)
- literals, comparisons, `and/or/not`, `in`, comprehensions, `len/str/int/set/...`

Anything else (imports, dunder attributes, calls on foreign names) is rejected
at load time with file + reason — that's the AST whitelist. If you need a new
helper, add it to `_IndexView` in `atlasix/rules.py` (core change, not pack).

## Checklist before shipping a pack

- [ ] build succeeds, entity counts match reality
- [ ] `who-uses` on 2–3 known anchors returns expected consumers
- [ ] duplicates clusters are real copy-paste (open 2 members, diff)
- [ ] rules: every finding is actionable; no rule fires on everything
- [ ] pack has a `#` header comment saying what domain it covers
