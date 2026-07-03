Use `grep` to search file contents with a regular expression.

Usage:
- Fast content search that works with any codebase size.
- Supports full regex syntax (e.g. "log.*Error", "function\s+\w+").
- Narrow the search with the `path` parameter (a file or directory); it defaults to the current working directory.
- Returns file paths and line numbers with matching lines.
- Respects .gitignore and .codeignore and skips files you should not read (.venv, .pyc, etc.).
- Use this tool when you need to find where functions are defined, how variables are used, or to locate specific error messages.
- For open-ended searches that may require multiple rounds of searching, use the `task` tool instead.
