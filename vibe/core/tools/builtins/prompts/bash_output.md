Use the `bash_output` tool to read the current stdout, stderr and status of a background bash process previously started with `bash(command=..., run_in_background=True)`.

**When to use:**
- You kicked off a long-running process (dev server, watcher, build, `tail -f`) in background mode and now want to check on it.
- You want to verify a background process is still running, or find out its exit code after it has finished.

**Arguments:**
- `bash_id` — the identifier returned by the original `bash` call that used `run_in_background=True`.

**What you get back:**
- `status`: one of `running`, `exited`, `terminated`, or `unknown`.
- `returncode`: set once the process has exited. `None` while `running`.
- `stdout` and `stderr`: the **tail** of each stream, capped to a size similar to the foreground `bash` tool. If the full stream on disk is larger than the cap, `stdout_truncated` / `stderr_truncated` will be `true`.
- `command`: the original command that was started (for confirmation).

**Typical flow:**
1. Start the process: `bash(command="npm run dev", run_in_background=True)` → returns `bash_id="ab12cd34..."`.
2. After letting it run for a moment, check: `bash_output(bash_id="ab12cd34...")`.
3. Inspect `status` and `stdout` to decide whether the server is up, whether tests are still failing, etc.
4. Call `bash_output` again later if you want fresh output — each call returns the latest tail.

**Limits:**
- You can only read background processes started in the *current* session. Clearing the session history terminates all background processes and makes their ids invalid.
- Calling `bash_output` with an unknown `bash_id` raises a tool error.
- The tool does not stream output — each call returns a snapshot. If you need to watch output evolve, call it multiple times with a short delay between calls.
