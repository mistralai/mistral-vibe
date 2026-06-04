Use `edit` to make exact string replacements in files.

**Arguments:**
- `file_path`: The path to the file to modify
- `old_string`: The exact text to find and replace
- `new_string`: The replacement text (must differ from old_string)
- `replace_all`: Set to `true` to replace all occurrences (default: `false`)

**IMPORTANT:**

- **ALWAYS** call `read` on the target before `edit`. The on-disk content may have changed since you last saw it (user edits, prior tool calls, external processes). Operating on stale content will either fail the exact-match check or silently apply the edit to the wrong place.
- The `old_string` must match the file content exactly, including whitespace and indentation
- When editing text from `read` output, match only the content AFTER the line number prefix (the `     1→` part is not in the file). Never include any part of the line number prefix in old_string or new_string.
- If `old_string` appears multiple times, the edit will fail unless `replace_all` is `true`. Either provide more surrounding context to uniquely identify the target, or set `replace_all` to `true`.
- Use `replace_all` for renaming variables or strings across the file
- Prefer editing existing files over writing new ones
- `old_string` cannot be empty; use `write_file` to create new files
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- If an `edit` fails because the `old_string` was not found, re-read the file before retrying — do not guess at variations.
