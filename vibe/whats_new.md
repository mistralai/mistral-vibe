# What's new in v2.21.0

- **Hooks are now stable** (previously experimental). Three types: `post_agent` (after an agent turn), `pre_tool` (before a tool runs), `post_tool` (after a tool runs). Declared in `.vibe/hooks.toml` or `~/.vibe/hooks.toml`; no opt-in flag. Breaking: types renamed from the experimental version (`post_agent_turn` → `post_agent`, `before_tool` → `pre_tool`, `after_tool` → `post_tool`). Docs: https://docs.mistral.ai/vibe/code/cli/hooks.
- **@file mentions**: Mentioned files are now read automatically via `read_file` tool calls.
