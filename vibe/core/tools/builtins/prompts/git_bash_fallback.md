Use `git_bash` to run a one-off Git Bash command on native Windows and capture
its output.

Usage:
- Each command runs independently in a fresh, stateless Git Bash process.
- Vibe resolves a usable `bash.exe` from PATH, Git for Windows, or standard Git install locations unless a `bash.exe` `shell` override is provided.
- Use POSIX shell syntax and Unix-style command chaining, redirects, variables, and quoting.
- Use the `timeout` or `timeout_seconds` parameter to control how long a command may run.
- Prefer the dedicated tools over their shell equivalents:
  - reading files -> `read_file`
  - creating files -> `write_file`; modifying files -> `edit`
  - searching -> `grep`
- Appropriate uses: git operations, running tests and build tools, package management, and quick system checks.
