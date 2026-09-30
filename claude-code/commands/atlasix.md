---
description: Orientation in this repo via atlasix (profile / who-uses / duplicates / search)
argument-hint: [symbol or intent]
---

Use the `atlasix` CLI to orient in this repository for the request: $ARGUMENTS

1. If $ARGUMENTS looks like a symbol/path (dot-prefixed name, file path, job/module name):
   - Run `atlasix who-uses <target>` — reverse references, 2 hops.
   - For a file, also `atlasix show <path> --resolved`.
2. If $ARGUMENTS looks like a free-text intent (e.g. "how does the deploy through ansible work"):
   - Run `atlasix search "<intent>" --top 5`, then `atlasix show <cited path> --resolved` on the best hit.
3. Always also consider `atlasix duplicates` when about to add similar-looking code.

Cite findings as `path:line`. Never modify files during orientation. If the index is
missing, run `atlasix build --no-vectors` first and report entity counts.
