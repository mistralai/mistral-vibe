# What's New in 2.0.0

- **Subagents**: The agent can now delegate tasks to specialized sub-agents for more complex workflows.
- **Interactive Questions**: The agent can now ask you clarifying questions as it works.
- **Slash Commands**: You can now define your own custom slash commands through skills.
- **Auto-Update**: Vibe will now keep itself up to date automatically. Disable with `enable_auto_update = false` in `config.toml`.
- **MCP Servers**: Configurations now support environment variables and custom timeouts.
- **Agents System**: Modes have been replaced by a more flexible agents system.

### ⚠️ Breaking Changes
- Custom modes must be migrated to the new agent format.
- `workdir` setting in `config.toml` is no longer supported.
- `instructions.md` files are no longer supported.
- Heuristic regex matching is removed. To use a regex in `enabled_tools`, `disabled_tools`, `enabled_skills`, or `disabled_skills`, prefix it with `re:` (e.g., `re:serena.*`).
