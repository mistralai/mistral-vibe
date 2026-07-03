Use `write_file` to write a file to the local filesystem.

Usage:
- This tool creates a new file; it returns an error if the file already exists. Use `edit` to modify existing files.
- The `file_path` must be an absolute path.
- Parent directories are created automatically if they don't exist.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the user.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked.
