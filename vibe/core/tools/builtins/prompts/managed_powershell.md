Use `powershell` to run native Windows PowerShell commands that may run for a
while, need ongoing input, or should remain inspectable after the first tool
call.

**Key characteristics:**
- PowerShell only: Vibe resolves `pwsh.exe`, then `powershell.exe`, unless a PowerShell `shell` override is provided.
- `cmd.exe` is not used by this tool.
- Stateful sessions: each command gets a `session_id`, a PTY, and a durable log file.
- Merged terminal stream: PTY sessions combine stdout and stderr in the captured output; use `output`/`stdout` as the terminal stream and expect compatibility `stderr` to be empty.
- Background handling: use `powershell(background=true)` for dev servers, watchers, and long builds that should keep running.
- Soft foreground timeout: `powershell(background=false, hard_timeout=false)` waits for `timeout_seconds`, then returns a live session if the command is still running.
- Hard foreground timeout: `powershell(..., hard_timeout=true)` terminates the process tree when `timeout_seconds` expires and reports a timeout error.
- Long polling: `powershell_output(cursor=N, wait_seconds=N, max_bytes=N)` waits internally, aggregates output, and returns on process exit, output cap, kill/reset, or wait-window expiration.
- Interactive input: use `powershell_stdin(session_id=..., text="...\n")` to press Enter or drive prompts. Use `powershell_stdin(control=["ctrl_c"])` for supported control sequences.
- Session management: `powershell_sessions(action="list"|"inspect"|"kill"|"reset")` lists PowerShell sessions, inspects one session, kills exactly one `session_id`, or resets all PowerShell sessions. `inspect` and `kill` require a single `session_id`; `reset` ignores `session_id`.
- Log files: `powershell_log_file(action="read", session_id=...)` reads a session's full output file; `write`/`append` annotate it once the session has exited.
- Spill files: full output is always stored under `~/.vibe/shell-tool/sessions/`.

**Prefer dedicated tools when available:**
- Read files with `read_file`, not `Get-Content`, `type`, or `more` through the shell.
- Search files with `grep`, not `Select-String`, `findstr`, or recursive shell commands.
- Edit files with `edit` or `write_file`, not shell redirection or PowerShell mutation commands.

**Good uses:**
- Build and test commands such as `npm run build`, `uv run pytest`, and `dotnet test`.
- Dev servers and watchers such as `npm run dev`.
- Commands that ask for confirmation or provide a REPL.
- System checks, package manager inspection, and git commands.

**Examples:**
- Long build: `powershell(command="npm run build", timeout_seconds=60)`, then `powershell_output(session_id=..., cursor=..., wait_seconds=60)`.
- Dev server: `powershell(command="npm run dev", background=true)`, then poll with `powershell_output(wait_seconds=30)`.
- Prompt: `powershell(command="Read-Host Name", timeout_seconds=10, background=true)`, then `powershell_stdin(text="Ada\n")` and `powershell_output(wait_seconds=10)`.
- Interrupt: `powershell_stdin(control=["ctrl_c"])` sends Ctrl-C to the PTY session.
