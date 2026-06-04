Use `read` to read the content of a file with line numbers. It handles encoding safely.

- By default, it reads up to 2000 lines from the beginning of the file
- Output larger than 50KB returns an error; use `offset` and `limit` for larger files
- Results include line numbers in `     1→content` format (1-indexed)
- Use `offset` (1-indexed line number) and `limit` to read specific portions
- This is more efficient than using `bash` with `cat` or `wc`

**Strategy for large files:**

1. Call `read` without offset/limit to get the start of the file
2. If the output is too large, use `offset` and `limit` to read targeted sections
3. Prefer `grep` to find specific content rather than reading sequentially chunk by chunk
4. Do not call `read` more than 3 times on the same file without responding to the user first

**Do not read:**
- Model checkpoint directories or weight files (.bin, .safetensors, .pt, .gguf, optimizer states, etc.)
- Binary files of any kind
- Entire directory trees of training runs or large codebases. If the user provides paths to such files, treat them as references. Do not open them unless the user explicitly asks you to inspect a specific file.
