Use `read_file` to read the content of a file. It's designed to handle large files safely.

- By default, it reads from the beginning of the file.
- Use `offset` (line number) and `limit` (number of lines) to read specific parts or chunks of a file. This is efficient for exploring large files.
- The result includes `was_truncated: true` if the file content was cut short due to size limits.
- This is more efficient than using `bash` with `cat` or `wc`.

**Strategy for large files:**

1. Call `read_file` with a `limit` (e.g., 1000 lines) to get the start of the file.
2. If `was_truncated` is true, the file is large. STOP and assess: do you already have enough information to answer the user's question? If yes, respond immediately — do not keep reading.
3. If you need more, prefer targeted reads (e.g., jump to a specific offset, read the last 100 lines, search for a relevant section) over reading sequentially chunk by chunk.
4. Do not call `read_file` more than 3 times on the same file without responding to the user first.

**Do not read or explore:**
- Model checkpoint directories or weight files (.bin, .safetensors, .pt, .gguf, optimizer states, etc.)
- Binary files of any kind
- Entire directory trees of training runs or large codebases. If the user provides paths to such files, treat them as references. Do not open them unless the user explicitly asks you to inspect a specific file.
