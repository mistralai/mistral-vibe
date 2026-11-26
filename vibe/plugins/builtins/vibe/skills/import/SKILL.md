---
name: import
description: Import the user's setup from other coding CLIs (Claude Code, Codex, OpenCode) into Vibe — instructions, skills, subagents, commands, hooks, MCP servers, permissions, and plugins. Load when the user wants to switch to Vibe, import or reuse another CLI's configuration, or asks whether a file from another CLI works in Vibe.
---

# Import From Other CLIs

Translate the user's Claude Code, Codex or OpenCode setup into Vibe and write
what they approve. This skill does not explain how those CLIs work: learn it
from their files and official docs. Learn Vibe's formats from the `vibe:vibe`
skill; load it first. Do not guess.

## Where to look

| CLI | Config folders and files | Docs index |
| --- | --- | --- |
| Claude Code | `~/.claude/`, `~/.claude.json`, `CLAUDE.md`, `.claude/`, `.mcp.json` | https://code.claude.com/docs/llms.txt |
| Codex | `$CODEX_HOME` or `~/.codex/`, `.codex/` | https://developers.openai.com/codex/llms.txt |
| OpenCode | `~/.config/opencode/`, `opencode.json(c)`, `.opencode/` | https://opencode.ai/llms.txt |

Fetch the docs index, then the page for each kind of item you found (settings,
memory or instructions, skills, subagents, commands, hooks, MCP, permissions,
plugins). Docs describe the latest version; when they disagree with the
user's files, trust the files.

## Steps

1. **Scan** the paths above in the home directory and the current project.
   Skip missing ones. Also read Vibe's config (`~/.vibe/`, `.vibe/`) so you
   never duplicate or overwrite an item.
2. **Learn.** For each kind of item found, read the source CLI's doc page and
   the matching `vibe:vibe` section. Note what has no Vibe equivalent.
3. **Plan.** Show one table: item, source, Vibe target, status (`ready`,
   `changed`, `not portable`), and a reason for anything not `ready`. Home
   items go to `~/.vibe/`, project items to `.vibe/` (trusted folder only).
   Wait for approval.
4. **Write** the approved items. Back up any Vibe file before editing it. Never
   edit the other CLI's files without asking.
5. **Check.** Ask the user to restart Vibe (hooks and agents load only at
   startup, `/reload` is not enough), read the Warnings box, and look for
   failed servers in `/mcp`. Fix what they report.
6. **Report** what was imported, what changed on the way, and what was left
   behind and why.

## Rules

- **Never weaken a guard.** A translated permission or hook must block at
  least what the original blocked, under every Vibe agent. If you cannot show
  it does, mark it `not portable`.
- **Never copy secrets.** Leave placeholders the user fills in. Never read
  `.env` files.
- **Show every command** a hook or MCP stdio server will run before writing it.
- **Test every hook script** with a sample Vibe payload on stdin before
  registering it.
- **Reuse before copying.** `AGENTS.md` and `.agents/skills/` are already
  shared. Point `skill_paths` at other skill folders when their skills are
  valid in Vibe.
- **Say when you don't know.** If neither the docs nor `vibe:vibe` settle a
  mapping, mark the item `not portable` and explain why.
