Use `edit` to perform exact string replacements in files.

Usage:
- You must use the `read_file` tool at least once before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from `read_file` output, preserve the exact indentation as it appears after the line-number prefix. Never include any part of the line-number prefix in `old_string` or `new_string`.
- The edit will fail if `old_string` is not found, or if it is found multiple times. Provide more surrounding context to uniquely identify the target, or set `replace_all` to true.
- Use `replace_all` to replace and rename every occurrence of a string across the file.
- `old_string` cannot be empty; use `write_file` to create new files.
- If an edit fails because `old_string` was not found, re-read the file before retrying — do not guess at variations.
- ALWAYS prefer editing existing files. Only use emojis if the user explicitly requests it.
