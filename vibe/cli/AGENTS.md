# vibe/cli - CLI & TUI Interface

CLI entry points and Textual-based terminal UI (65 files).

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Main entry | `entrypoint.py` | `vibe` command |
| CLI args | `cli.py` | Argument parsing |
| TUI app | `textual_ui/app.py` | Main Textual app |
| Chat input | `textual_ui/widgets/chat_input/` | Input widget + completions |
| Slash commands | `commands.py` | `/help`, `/compact`, etc. |
| Autocomplete | `autocompletion/` | Path + slash completion |
| Update notifier | `update_notifier/` | Auto-update system |

## ENTRY POINTS

- `vibe` → `vibe.cli.entrypoint:main`
- `vibe-acp` → `vibe.acp.entrypoint:main`

## CONVENTIONS

- Inherits all root conventions
- Textual widgets subclass `Widget` or `Static`
- Event handlers in `handlers/event_handler.py`
- Notifications via `notifications/` adapters
- All UI state managed in app

## ANTI-PATTERNS

- No direct print/logging - use Textual log
- No blocking in UI thread - use `run_worker()`
- No raw ANSI - use `Rich` or `ansi_markdown.py`

## NOTES

- Uses `textual` >=7.4.0 for TUI
- File path completion via `@file` syntax
- External editor via `Ctrl+G`
- Multi-line input via `Ctrl+J` or `Shift+Enter`
