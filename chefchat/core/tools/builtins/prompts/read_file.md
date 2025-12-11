Use `read_file` to read the content of a file. It's designed to handle large files safely.

- By default, it reads from the beginning of the file.
- Use `offset` (line number) and `limit` (number of lines) to read specific parts or chunks of a file. This is efficient for exploring large files.
- The result includes `was_truncated: true` if the file content was cut short due to size limits.

**Strategy for large files:**

1. Call `read_file` with a `limit` (e.g., 1000 lines) to get the start of the file.
2. If `was_truncated` is true, you know the file is large.
3. To read the next chunk, call `read_file` again with an `offset`. For example, `offset=1000, limit=1000`.

This is more efficient than using `bash` with `cat` or `wc`.
