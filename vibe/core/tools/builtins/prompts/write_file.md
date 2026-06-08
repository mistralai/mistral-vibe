Use `write_file` to create a new file.

**Arguments:**
- `path`: The file path (relative or absolute)
- `content`: The content to write to the file

**BEHAVIOR:**

- `write_file` can ONLY create new files.
- If the file already exists, the tool returns an error. Use `edit` to modify existing files.
- Parent directories are created automatically if they don't exist.

**BEST PRACTICES:**

- **NEVER** use `write_file` to modify an existing file — it will fail. Use `edit` instead.
- **NEVER** write new files unless explicitly required — prefer modifying existing files via `edit`.
- **NEVER** proactively create documentation files (*.md) or README files unless explicitly requested.
- **AVOID** using emojis in file content unless the user explicitly requests them.

**Usage Example:**

```python
write_file(
    path="src/new_module.py",
    content="def hello():\n    return 'Hello World'"
)
```
