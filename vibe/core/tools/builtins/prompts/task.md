Use `task` to launch a subagent that handles a complex, multi-step task autonomously.

Usage:
- Specify the `agent` (subagent type) that best fits the work; see the available subagents in your system prompt.
- Provide a detailed, self-contained task description and state exactly what the subagent should return, since it runs autonomously and its only output is a final message.
- Prefer direct tools for simple lookups: to read a specific file use `read_file`, and to find a specific symbol use `grep`, rather than spawning a subagent.
- Launch multiple subagents in parallel for independent work; once delegated, do not duplicate that work yourself.
- The subagent's result is not shown to the user — summarize it back to them yourself.
- Subagents run read-only: they cannot modify files or ask the user questions.
