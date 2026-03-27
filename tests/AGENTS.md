# tests/ — Test Suite

Root-level pytest test suite (~76 files). Tests mirror package structure under `tests/`.

## STRUCTURE

```
tests/
├── __init__.py
├── conftest.py           # Shared fixtures, config
├── acp/                  # ACP protocol tests (20 files)
├── cli/                  # CLI & TUI tests (14 files)
│   └── textual_ui/       # Textual UI component tests
├── core/                 # Core system tests (17 files)
│   ├── agent_loop/       # Agent orchestration
│   ├── tools/            # Tool execution tests
│   └── llm/backend/      # LLM provider tests
├── snapshots/            # Snapshot test data
├── stubs/                # Test dependency stubs
├── tools/                # Standalone tool tests (12 files)
├── autocompletion/       # Autocomplete system tests (8 files)
└── update_notifier/      # Update notification tests (7 files)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Shared fixtures | `conftest.py` | Reusable test utilities |
| ACP protocol | `tests/acp/` | Editor integration tests |
| TUI components | `tests/cli/textual_ui/` | UI widget tests |
| Tool execution | `tests/core/tools/`, `tests/tools/` | Built-in + MCP tools |
| Snapshot tests | `tests/snapshots/` | UI state snapshots |

## TEST PATTERNS

- **pytest-based** — Standard pytest conventions
- **Mirror structure** — Tests follow package layout (`vibe/cli/` → `tests/cli/`)
- **Snapshot testing** — UI state captured in `__snapshots__/` subdirectories
- **Async support** — `pytest-asyncio` for async test functions
- **Fixture reuse** — Shared fixtures in root `conftest.py`

## CONVENTIONS

- **Test naming**: `test_*` or `*_test.py` patterns
- **Async tests**: Use `@pytest.mark.asyncio` decorator
- **Fixtures**: Register via `@pytest.fixture` in conftest
- **Mocking**: Use `unittest.mock` for external dependencies
- **Snapshot format**: TOML files under `__snapshots__/`

## ANTI-PATTERNS

- No test execution in fixtures (lazy setup)
- No hard-coded API keys — use environment variables
- No snapshot drift — regenerate with `--update-snapshots` flag
- No blocking calls in async tests
