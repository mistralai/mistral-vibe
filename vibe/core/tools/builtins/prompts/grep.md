Use `grep` to recursively search for a regular expression pattern in files.

- It's very fast and automatically ignores files that you should not read like .pyc files, .venv directories, etc.
- Use this to find where functions are defined, how variables are used, or to locate specific error messages.
- Narrow the search with `glob` (e.g. `*.py`, `**/*.ts`) to only scan matching files.
- `output_mode` controls the shape of the result:
  - `content` (default) — matching lines with line numbers.
  - `files_with_matches` — just the paths of files that contain a match (cheap overview).
  - `count` — per-file match counts.
- In `content` mode, `context_before` / `context_after` add surrounding lines (like grep's `-B` / `-A`).
- Set `ignore_case` to force case-insensitive matching (default is smart-case). Set `multiline` to let a pattern span lines (ripgrep only).
- Use the `glob` tool instead when you only need to find files by name, not search contents.
