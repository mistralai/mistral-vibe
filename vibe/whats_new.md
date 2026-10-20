# What's new in v3.0.1

Vibe 3.0 is here: a brand-new terminal UI, and one agent harness across the CLI, VS Code and the browser.

**A new terminal UI, written in Rust**

- **Instant startup**: Vibe appears almost immediately, and you can type your first prompt while it finishes starting
- **Smooth in long sessions**: scrolling, typing and pickers stay fluid, even hundreds of turns in
- **Point and click**: click tool approvals, questions and picker options, and drag scrollbars smoothly
- **Branch a conversation**: `/branch` forks the current session into a new one you can resume later

**One harness everywhere**

- **Same agent in every surface**: the CLI, the VS Code extension and Mistral Vibe in the browser now run the same agent harness, so it behaves the same wherever you use it
- **`/teleport` keeps its harness**: a session sent to the browser keeps running on the harness it started on
- **Patient agent**: the agent can wait for your next message or for background work to finish, instead of polling

**Out with the old**

- The Python TUI, the legacy harness and plan mode are gone. Your config, skills, agents, MCP servers and sessions carry over.

Something off? Tell us on [GitHub](https://github.com/mistralai/mistral-vibe/issues).
