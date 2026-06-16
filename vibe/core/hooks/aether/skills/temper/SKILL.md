---
name: temper
description: Review the current staged diff before committing. On success, writes .vibe/.temper_ok to clear the temper gate for this commit.
user-invocable: true
allowed-tools: bash read
---

Review the staged diff for this commit. Start by running:

```bash
git diff --stat --cached
git diff --cached
```

Then critique the diff across three dimensions:

**Correctness** — logic errors, off-by-ones, wrong assumptions, broken invariants.

**Security** — injection risks, credential exposure, trust boundary violations, insecure defaults.

**Design** — coupling violations, API contract breaks, naming that misleads, abstractions that leak.

Rate each finding: 🔴 blocker / 🟡 significant / 🟢 minor

Output a markdown table:

```
| # | Dimension | Severity | File | Finding | Recommendation |
```

Followed by counts: Blockers: N, Significant: N, Minor: N

If no blockers (🔴), write the marker file to clear the gate. The file must contain
the SHA256 hash of the current staged diff so that any subsequent `git add` invalidates
the approval:

```bash
mkdir -p .vibe && git diff --cached | python3 -c "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())" > .vibe/.temper_ok
```

Then confirm: "✓ temper gate cleared — safe to commit."

If blockers were found, do NOT write the marker. State: "X blocker(s) found. Fix these before committing, or append # temper:skip to bypass."
