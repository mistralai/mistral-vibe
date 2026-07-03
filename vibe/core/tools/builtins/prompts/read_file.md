Use `read_file` to read a file from the local filesystem. If the path does not exist, an error is returned.

Usage:
- The `file_path` parameter should be an absolute path.
- By default, this tool returns up to 2000 lines from the start of the file.
- The `offset` parameter is the line number to start from (1-indexed); use it with `limit` to read later sections of a large file.
- Output is capped at 50KB; if a file is larger, page through it with `offset` and `limit`.
- Use the `grep` tool to find specific content in large files instead of reading sequentially.
- Contents are returned with each line prefixed by its line number.
- Call this tool in parallel when you want to read multiple files.
- Do not read binary files or model weight/checkpoint files (.bin, .safetensors, .pt, .gguf, etc.); treat such paths as references unless explicitly asked to inspect a specific file.
