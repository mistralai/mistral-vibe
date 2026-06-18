Use `glob` to find files by name pattern, fast.

- Patterns support `*`, `?`, character classes, and `**` for recursive matching (e.g. `**/*.py`, `src/**/test_*.ts`).
- Results are returned newest-first (by modification time), so the most recently touched files surface first.
- Common noise directories (`.git`, `node_modules`, `.venv`, `__pycache__`, build/dist, ...) are pruned automatically.
- Prefer this over `bash` with `find`/`ls` when you know the filename shape but not the exact location. Use `grep` instead when you need to search file *contents*.
