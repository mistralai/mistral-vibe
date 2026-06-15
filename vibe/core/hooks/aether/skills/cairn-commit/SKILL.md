---
name: cairn-commit
description: Generate a strong commit message from the staged diff, following conventional commits format. Use instead of writing a message manually.
user-invocable: true
allowed-tools: bash
---

Generate a commit message for the currently staged changes.

First run:
```bash
git diff --stat --cached
git diff --cached
```

Then write a commit message following this format:

```
<type>(<scope>): <short imperative summary under 72 chars>

<optional body: what changed and why, wrapped at 72 chars>

<optional footer: breaking changes, issue refs>
```

Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `build`, `ci`

Rules:
- Summary line: imperative mood ("add X", not "added X" or "adds X"), no period at end
- Body: explain the WHY, not the what (the diff shows the what)
- If multiple concerns are staged, suggest splitting into separate commits instead of writing one giant message

Output just the commit message, ready to copy-paste. Do not run `git commit` — let the user review and run it themselves.

If no changes are staged, say so and stop.
