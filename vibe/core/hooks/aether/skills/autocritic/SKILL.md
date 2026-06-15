---
name: autocritic
description: Critique the current plan for blockers before committing. Writes output to .claude/plans/CRITIQUE.md, which clears the whetstone gate.
user-invocable: true
allowed-tools: bash read write_file
---

Find the most recently modified `.md` file in `.claude/plans/` (excluding `CRITIQUE.md`) and critique it as a senior engineer would. If no plans directory exists, say so and stop.

Run three passes over the plan:

**Pass 1 — Implementation** (senior engineer lens)
Check for: missing implementation details, unhandled edge cases, incorrect assumptions about existing code or libraries, missing tests.

**Pass 2 — Architecture** (systems architect lens)
Check for: coupling violations, leaky abstractions, scalability landmines, painful-to-change-later decisions.

**Pass 3 — Risk** (cautious senior engineer lens)
Check for: security issues, data loss scenarios, breaking API/contract changes, anything that would be a bad surprise at 2am.

Rate each finding: 🔴 blocker / 🟡 significant / 🟢 minor

Output a markdown table:

```
| # | Pass | Severity | Finding | Recommendation |
```

Followed by counts: Blockers: N, Significant: N, Minor: N

Then write the full critique output to `.claude/plans/CRITIQUE.md`, prepending a header line:
`# Critique — <plan filename> — <today's date>`

If the file already exists, append (do not overwrite) so history accumulates.

After writing, confirm: "Critique written to .claude/plans/CRITIQUE.md — whetstone gate cleared."

If blockers (🔴) were found, end with: "X blocker(s) found. Resolve these before committing, or append # whetstone:skip to bypass."
If only 🟡/🟢, end with: "No blockers — safe to commit."
