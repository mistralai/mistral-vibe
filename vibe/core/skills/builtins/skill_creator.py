from __future__ import annotations

from vibe.core.skills.models import SkillInfo, SkillSource

_PROMPT = """# Skill Creator

Use this when the user asks you to create, update, or delete a Vibe skill.

## Gathering requirements

When creating a new skill, ask the user for the details you are missing. **Ask
one question at a time and wait for the answer before asking the next.** Never
send multiple questions in a single message — it is bewildering. Gather, in
order:

1. Name — a slug (lowercase, hyphens), e.g. `run-migrations` or
   `format-changelog`.
2. Purpose / description — when should this skill load? This is the routing text
   the model sees.
3. Instructions — what should the skill actually tell the model to do? Steps,
   conventions, commands, gotchas.

Skip any question the user has already answered. Once you have all three,
confirm the scope (project vs global) if it is not obvious, then write the
skill.

## What a skill is

A skill is a directory containing a `SKILL.md` file with YAML frontmatter plus
Markdown instructions. The model loads it on demand via the `skill` tool; a
user-invocable skill is also reachable by typing `/skill-name`. The `description`
is always visible for routing — the body is loaded only after the skill is
selected.

## Where skills live (discovery order, first match wins)

1. `skill_paths` entries from `config.toml`
2. `.vibe/skills/` — project scope (requires a trusted folder)
3. `.agents/skills/` — project scope (requires a trusted folder)
4. `~/.vibe/skills/` — user global
5. `~/.agents/skills/` — user global

Precedence: built-in skills are seeded first and their names are **reserved** —
a user skill whose name collides with a built-in is silently skipped at load
time and never appears. Among the paths above, the first directory to define a
given name wins; later duplicates are skipped. Built-in skills are read-only.
Registry skills are materialized under `VIBE_HOME` and should not be
hand-edited.

Choose the scope before writing: use `.vibe/skills/<name>/` for a skill scoped
to this project, or `~/.vibe/skills/<name>/` for one available everywhere. Ask
the user which they want when it is not obvious, and write to the directory that
matches the chosen scope.

## SKILL.md format

```markdown
---
name: my-skill
description: Load this skill when ...
---

# My Skill

Instructions go here.
```

Frontmatter fields:

- `name` (required): lowercase letters, numbers, and hyphens only
  (`^[a-z0-9]+(-[a-z0-9]+)*$`), 1–64 chars. It should match the directory name
  (a mismatch only logs a warning; the skill still loads under the frontmatter
  name). Do not reuse a built-in skill name (e.g. `vibe`) or a name already
  taken by another skill — the collision is silently skipped and the skill will
  not load.
- `description` (required): 1–1024 chars. Say *when* to load the skill — this is
  the only text the model sees before selecting it.
- `user-invocable` (optional, default `true`): when `false` the skill is
  model-only — hidden from the `/` menu and not reachable via `/skill-name`.
- `allowed-tools` (optional, experimental): space-delimited list of pre-approved
  tools.
- `license`, `compatibility`, `metadata` (optional): metadata only. `metadata`
  is a flat string-to-string map (e.g. `display-name`, `short-description`).

Do not invent frontmatter keys. Fields from other products (e.g. `visibility`,
`defaultEnabled`) are not part of Vibe's schema and are ignored.

## Support files

Keep `SKILL.md` focused on durable instructions. Add Markdown, plain text, CSV,
JSON, or YAML support files in the same directory only when they earn their
place. Reference them by path relative to the skill directory; the model reads
them with `read_file` on demand.

## Create, update, delete

- Create: make `<skills-dir>/<slug>/SKILL.md` with valid frontmatter, where
  `<skills-dir>` is the directory for the chosen scope (`.vibe/skills/` for
  project, `~/.vibe/skills/` for global). Add support files under the same
  directory only when useful.
- Update: edit `SKILL.md` or support files in place with `edit`. Preserve useful
  existing guidance; keep the diff minimal.
- Delete: remove the whole skill directory when asked to delete a skill. Do not
  delete only `SKILL.md`.

## Applying changes

Writing under a skills directory goes through the normal `write_file` / `edit`
permission prompts — there is no separate proposal step. After changes, tell the
user they can run `/reload` to pick up the new or edited skill without
restarting. Keep the final summary brief; do not paste full file contents unless
the user asks."""

SKILL = SkillInfo(
    name="skill-creator",
    description=(
        "Load this skill before creating, updating, or deleting a Vibe skill, "
        "or whenever you plan to add or modify SKILL.md files under a skills directory. "
        "It explains Vibe's SKILL.md frontmatter, discovery order, support files, "
        "and the permission flow."
    ),
    user_invocable=True,
    prompt=_PROMPT,
    source=SkillSource.BUILTIN,
)
