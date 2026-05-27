# What's new in v2.12.1

- **Custom compaction prompts**: Override the default `/compact` prompt by setting `compaction_prompt_id` and dropping a markdown file in `~/.vibe/prompts/` or `.vibe/prompts/`.
- **Safer programmatic mode**: `-p` no longer auto-approves tool calls by default — pass `--auto-approve` to restore the previous behavior.
- **Teleport Vibe Code Web**: `/teleport` now uses the new Vibe Code Web sessions.
