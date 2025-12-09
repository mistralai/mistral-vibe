Use `write_file` to write content to a file.

**Arguments:**
- `path`: The file path (relative or absolute)
- `content`: The content to write to the file
- `overwrite`: Must be set to `true` to overwrite an existing file (default: `false`)

**IMPORTANT SAFETY RULES:**

- By default, the tool will **fail if the file already exists** to prevent accidental data loss
- To **overwrite** an existing file, you **MUST** set `overwrite: true`
- To **create a new file**, just provide the `path` and `content` (overwrite defaults to false)
- If parent directories don't exist, they will be created automatically

**BEST PRACTICES:**

- **ALWAYS** use the `read_file` tool first before overwriting an existing file to understand its current contents
- **ALWAYS** prefer using `search_replace` to edit existing files rather than overwriting them completely
- **NEVER** write new files unless explicitly required - prefer modifying existing files
- **NEVER** proactively create documentation files (*.md) or README files unless explicitly requested
- **AVOID** using emojis in file content unless the user explicitly requests them

**Usage Examples:**

```python
# Create a new file (will error if file exists)
write_file(
    path="src/new_module.py",
    content="def hello():\n    return 'Hello World'"
)

# Overwrite an existing file (must read it first!)
# First: read_file(path="src/existing.py")
# Then:
write_file(
    path="src/existing.py",
    content="# Updated content\ndef new_function():\n    pass",
    overwrite=True
)
```

**Remember:** For editing existing files, prefer `search_replace` over `write_file` to preserve unchanged portions and avoid accidental data loss.
