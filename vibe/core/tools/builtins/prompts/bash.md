Execute a shell command and return its output.

- Prefer absolute paths. Shell state — working directory, environment variables, functions — does NOT persist between calls; each call starts a fresh shell from the user's profile.
- Prefer the dedicated tools over shell utilities: use `read_file` instead of `cat`/`head`/`tail`, `grep` instead of `grep`/`sed`/`awk` for searching, and `edit`/`write_file` instead of `sed`/`echo` redirects. Only fall back to the shell utility if a dedicated tool genuinely cannot do the task.
- Commands run under a permission model (allow / deny / ask). A denied command means the user declined it — adjust your approach, don't retry the same command verbatim.
- `timeout` is in seconds (default 300). The command is killed if it exceeds the timeout; there is no background execution, so avoid launching long-running or blocking processes.

# Git
- Interactive flags (`-i`, e.g. `git rebase -i`, `git add -i`) are not supported in this environment.
- Use the `gh` CLI for GitHub operations (PRs, issues, API).
- Commit or push only when the user asks. If you are on the default branch, create a branch first.
- Do not append your own commit/PR co-authoring footer — the signature is added automatically.
