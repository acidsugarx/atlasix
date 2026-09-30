---
description: Atlasix acceptance check — rebuild index and lint (duplication + unresolved refs)
---

Run the atlasix acceptance check for recent changes in this repository:

1. `atlasix build --no-vectors --json` — report the stats line.
2. `atlasix lint` — report findings grouped by rule id; `severity: error` findings must be fixed.
3. If the user gives a symbol in $ARGUMENTS, also run `atlasix who-uses <symbol>` and
   summarize what consumes it.

Verdict: duplication warnings must not grow vs. previous run, unresolved-ref findings
must not appear. State the verdict explicitly.
