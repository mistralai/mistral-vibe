Use the `bash` tool for shell commands that may run for a while, need ongoing
input, or should remain inspectable after the first tool call.

**Key characteristics:**
- Stateful sessions: each command gets a `session_id`, a PTY, and a durable log file.
- Merged terminal stream: PTY sessions combine stdout and stderr in the captured output; use `output`/`stdout` as the terminal stream and expect compatibility `stderr` to be empty.
- Background handling: use `bash(background=true)` for dev servers, watchers, and long builds that should keep running.
- Soft foreground timeout: `bash(background=false, hard_timeout=false)` waits for `timeout_seconds`, then returns a live session if the command is still running.
- Hard foreground timeout: `bash(..., hard_timeout=true)` kills the process group when `timeout_seconds` expires and reports a timeout error.
- Long polling: `bash_output(cursor=N, wait_seconds=N, max_bytes=N)` waits internally, aggregates output, and returns on process exit, output cap, kill/reset, or wait-window expiration. `cursor` is a byte offset into the log; pass the `next_cursor` from the previous call to resume without re-reading.
- Interactive input: use `bash_stdin(session_id=..., text="...\n")` to press Enter or drive prompts, REPLs, and installers. Use `bash_stdin(control=["ctrl_c"])` for control bytes.
- Session management: `bash_sessions(action="list"|"inspect"|"kill"|"reset")` to enumerate, inspect, terminate, or clear sessions (`reset(clear_logs=true)` also deletes stored logs).
- Log files: `bash_log_file(action="read", session_id=...)` reads a session's full output file; `write`/`append` annotate it once the session has exited.
- Spill files: full output is always stored under `~/.vibe/bash-tool/`.

**Prefer dedicated tools when available:**
- Read files with `read`, not `cat`, `head`, `tail`, or `sed` through bash.
- Search files with `grep`, not `grep`, `find`, or `rg` through bash.
- Edit files with `edit` or `write_file`, not shell redirection or `sed -i`.

**Good uses:**
- Build and test commands such as `npm run build`, `uv run pytest`, and `cargo test`.
- Dev servers and watchers such as `npm run dev`.
- Commands that ask for confirmation or provide a REPL.
- System checks, package manager inspection, and git commands.

**Examples:**
- Long build: `bash(command="npm run build", timeout_seconds=60)`, then `bash_output(session_id=..., cursor=..., wait_seconds=60)`.
- Dev server: `bash(command="npm run dev", background=true)`, then poll with `bash_output(wait_seconds=30)`.
- Prompt: `bash(command="some-installer", timeout_seconds=10)`, then `bash_stdin(text="y\n")` and `bash_output(wait_seconds=10)`.
- Interrupt: `bash_stdin(control=["ctrl_c"])` sends Ctrl-C to the PTY session.
