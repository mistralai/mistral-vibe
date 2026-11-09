---
name: write-vibe-adr
description: Create or update Architecture Decision Records for the Mistral Vibe Python CLI. Use when a design discussion creates a new architectural constraint, when an undocumented convention causes confusion or review feedback, or when architecture guidance in docs/adr or AGENTS.md must be changed.
metadata:
  display-name: Write Vibe ADR
  short-description: Create architecture ADRs for Vibe
  default-prompt: Use $write-vibe-adr to document a Vibe architecture decision and register it for agents.
---

# Write Vibe ADR

Vibe ADRs are concise, agent-facing architecture rules in `docs/adr/`. Writing an ADR is a two-step process: write the document, then register it in the Architecture Decisions table in `AGENTS.md`.

## When To Create An ADR

- A design discussion creates a new architecture rule, constraint, or direction.
- An existing convention is undocumented and caused a bug, review comment, or repeated confusion.
- A pattern applies across a Vibe area or boundary, not just one file.
- Future work should follow a direction that may differ from the current architecture.

Do not create ADRs for one-off implementation choices, formatting rules, or behavior already enforced by linting.

## Steps

### 1. Confirm the scope

Use one ADR when the decision is cohesive. Split ADRs when separate agents would need to read them for unrelated change areas.

Use the Architecture Decisions table in `AGENTS.md` as the current topic
index. Ownership lives in `0002`, feature-module rules in `0018`, and the
client protocol boundary in `0009`. Keep implementation walkthroughs in
`docs/reference/` and link them from the concise decision.

### 2. Determine the next number

```bash
ls docs/adr/
```

Use the next sequential number, zero-padded to 4 digits: `NNNN-my-decision.md`.
Do not reuse a number or rename an existing ADR solely to change its title.

### 3. Write the ADR

Use this format:

```markdown
# 0009 Decision Title

## Decision

What we decided. Include concrete rules and current-vs-aspirational direction when relevant.

## Rationale

Why this decision exists. Name the tradeoff and any non-obvious consequences.
Mention a rejected alternative only when it helps explain the decision.

## Agent Guidance

- Concrete instructions an agent should follow while changing code.
- Keep guidance task-oriented and easy to scan.

## Flag To User When

- Situations where an agent must stop and ask because current code or user request conflicts with the ADR direction.
```

Keep ADRs concise. Match the existing 20-50 line style. Do not add a status field.
Give each rule one authoritative ADR; link to it rather than restating it.
Decision states constraints, Agent Guidance gives their concrete application,
and Flag To User When lists only topic-specific exceptions. Keep detailed
implementation mechanics in reference pages. When merging decisions, preserve
the old path with a short supersession notice and update the task index.

### 4. Register in AGENTS.md

Add or update a row in the Architecture Decisions table:

```markdown
| <task trigger> | [0009 Decision Title](docs/adr/0009-my-decision.md) |
```

The trigger text must describe what the agent is changing, not vague architecture language.

Good triggers:

- "Adding a new delivery surface, protocol bridge, or UI-owned behavior"
- "Changing tool args/results, permissions, output limits, or adapters"
- "Changing session transcript shape, metadata, resume, rewind, or migrations"

Bad triggers:

- "Working on architecture"
- "When relevant"
- "Making decisions"

If the ADR is not registered in `AGENTS.md`, agents will not reliably discover it.

## Common Mistakes

| Mistake | Fix |
| --- | --- |
| Writing the ADR but not updating `AGENTS.md` | Always do both. |
| Using vague trigger text | Name concrete code-change scenarios. |
| Duplicating another ADR | Reference or update the existing ADR instead. |
| Writing a human essay | Keep it short, directive, and agent-facing. |
| Copying an implementation plan into the ADR | Keep only the durable rule and its consequences. |
| Adding status fields | Vibe ADRs intentionally mix current and aspirational decisions without status. |
