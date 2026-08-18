The shell tool is named `git_bash` — there is no `bash` tool, and calling `bash` fails.
Commands use POSIX/Git Bash syntax (not cmd.exe).

Use `git_bash` to run Git Bash commands on native Windows when commands may run
for a while, need ongoing input, or should remain inspectable after the first
tool call.

**Key characteristics:**
- Git Bash only: Vibe resolves a usable `bash.exe` from PATH, Git for Windows, or standard Git install locations unless a `bash.exe` `shell` override is provided.
- This tool uses POSIX shell syntax and Unix-style command chaining, redirects, variables, and quoting.
- Stateful sessions: each command gets a `session_id`, a PTY, and a durable log file.
- Background handling: use `git_bash(background=true)` for dev servers, watchers, and long builds that should keep running.
- Soft foreground timeout: `git_bash(background=false, hard_timeout=false)` waits for `timeout_seconds`, then returns a live session if the command is still running.
- Hard foreground timeout: `git_bash(..., hard_timeout=true)` terminates the process tree when `timeout_seconds` expires and reports a timeout error.
- Long polling: `git_bash_output(cursor=N, wait_seconds=N, max_bytes=N)` waits internally, aggregates output, and returns on process exit, output cap, kill/reset, or wait-window expiration.
- Interactive input: use `git_bash_stdin(session_id=..., text="...\n")` to press Enter or drive prompts. Use `git_bash_stdin(control=["ctrl_c"])` for supported control sequences.
- Session management: `git_bash_sessions(action="list"|"inspect"|"kill"|"reset")` lists Git Bash sessions, inspects one session, kills exactly one `session_id`, or resets all Git Bash sessions. `inspect` and `kill` require a single `session_id`; `reset` ignores `session_id`.
- Log files: `git_bash_log_file(action="read", session_id=...)` reads a session's full output file; `write`/`append` annotate it once the session has exited.
- Spill files: full output is always stored under `~/.vibe/shell-tool/sessions/`.

**Prefer dedicated tools when available:**
- Read files with `read_file`, not `cat`, `sed`, or shell loops.
- Search files with `grep`, not recursive shell commands.
- Edit files with `edit` or `write_file`, not shell redirection or mutation commands.

**Good uses:**
- Build and test commands such as `npm run build`, `uv run pytest`, and `make test`.
- Dev servers and watchers such as `npm run dev`.
- Commands that ask for confirmation or provide a REPL.
- System checks, package manager inspection, and git commands.

**Examples:**
- Long build: `git_bash(command="npm run build", timeout_seconds=60)`, then `git_bash_output(session_id=..., cursor=..., wait_seconds=60)`.
- Dev server: `git_bash(command="npm run dev", background=true)`, then poll with `git_bash_output(wait_seconds=30)`.
- Prompt: `git_bash(command="read -r name; echo answer=$name", timeout_seconds=10, background=true)`, then `git_bash_stdin(text="Ada\n")` and `git_bash_output(wait_seconds=10)`.
- Interrupt: `git_bash_stdin(control=["ctrl_c"])` sends Ctrl-C to the PTY session.
