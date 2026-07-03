Use `todo` to create and maintain a structured task list for the current session.

Usage:
- `action: "read"` returns the current list; `action: "write"` replaces it with the full `todos` list you provide (include every item you want to keep — any item not in the list is removed).
- Each todo has `id`, `content`, `status` (pending, in_progress, completed, cancelled), and `priority` (high, medium, low).
- Use proactively for multi-step work (3+ distinct steps), non-trivial tasks, or when the user provides multiple tasks. Skip it for single, trivial, or purely informational requests.
- Keep exactly one task `in_progress` at a time; mark it before starting and `completed` immediately after finishing.
- Only mark `completed` when the work is actually done (tests passing, no unresolved errors). If blocked, keep it `in_progress` and add a follow-up todo describing the blocker.
- Keep items specific and actionable; break large work into smaller steps.
