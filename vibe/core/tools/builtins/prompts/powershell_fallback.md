Use `powershell` to run a one-off PowerShell command on native Windows and
capture its output.

Usage:
- Each command runs independently in a fresh, stateless PowerShell process.
- Vibe resolves `pwsh.exe`, then `powershell.exe`, unless a PowerShell `shell` override is provided.
- `cmd.exe` is not used by this tool.
- Use the `timeout` or `timeout_seconds` parameter to control how long a command may run.
- Prefer the dedicated tools over their shell equivalents:
  - reading files -> `read_file`
  - creating files -> `write_file`; modifying files -> `edit`
  - searching -> `grep`
- Appropriate uses: git operations, running tests and build tools, package management, and quick system checks.
