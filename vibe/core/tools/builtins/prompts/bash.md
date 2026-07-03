Use `bash` to run a one-off shell command and capture its output.

Usage:
- Each command runs independently in a fresh, stateless environment.
- Use the `timeout` parameter (defaults to the configured limit) to control how long a command may run; raise it for long-running commands.
- Prefer the dedicated tools over their shell equivalents:
  - reading files → `read_file` (not `cat`, `head`, `tail`, `sed`, `less`)
  - creating files → `write_file` (not `echo >`); modifying files → `edit` (not `sed -i`, `awk`)
  - searching → `grep` (not `grep -r`, `find`, `rg`, `ag`)
- Appropriate uses: git operations, running tests and build tools, package management, and quick system checks (`pwd`, `ls`, `which`, `stat`).
