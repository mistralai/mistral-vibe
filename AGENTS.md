# PROJECT KNOWLEDGE BASE

**Generated:** 2026-03-14
**Commit:** 9421fbc
**Branch:** main

## OVERVIEW

Mistral Vibe is Mistral AI's open-source CLI coding assistant. Python 3.12+ CLI tool providing conversational interface to codebases using Mistral's models.

## STRUCTURE

```
mistral-vibe/
├── vibe/                    # Main package (NOT src/)
│   ├── cli/                 # CLI interface, TUI, autocompletion
│   ├── core/                # Agent loop, tools, LLM backends, plugins
│   ├── acp/                 # Agent Client Protocol (editor integration)
│   └── setup/               # Onboarding, trust folder setup
├── tests/                   # Test suite (root-level)
├── distribution/            # Zed editor extension
├── docs/                    # Documentation
└── scripts/                 # Build/release scripts
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Agent loop | `vibe/core/agent_loop.py` | Main orchestration |
| Tools | `vibe/core/tools/` | Built-in + MCP tools |
| LLM backends | `vibe/core/llm/backend/` | Mistral, Anthropic, Vertex |
| CLI entry | `vibe/cli/entrypoint.py` | `vibe` command |
| ACP entry | `vibe/acp/entrypoint.py` | `vibe-acp` command |
| Plugin system | `vibe/core/plugins/` | Built-in + custom plugins |
| Skills system | `vibe/core/skills/` | Extensibility |

## CODE MAP

| Symbol | Type | Location |
|--------|------|----------|
| `AgentLoop` | class | `vibe/core/agent_loop.py` |
| `Tool` | class | `vibe/core/tools/base.py` |
| `ToolManager` | class | `vibe/core/tools/manager.py` |
| `LLMBackend` | base | `vibe/core/llm/backend/base.py` |
| `TextualApp` | class | `vibe/cli/textual_ui/app.py` |

## CONVENTIONS

- **Python 3.12+** - Required version
- **No `src/`** - Package is `vibe/` at project root
- **Line length** - 88 chars (ruff default)
- **Import style** - `from __future__ import annotations` required
- **Relative imports** - Banned (`ban-relative-imports = "all"`)
- **Type stubs** - Required for external packages

## ANTI-PATTERNS (THIS PROJECT)

- No `src/` directory - source goes in `vibe/`
- No bare `except:` - use specific exceptions
- No `as any` type suppression
- No relative imports
- Max 50 statements per function, 15 branches

## COMMANDS

```bash
uv sync              # Install dependencies
uv run vibe          # Run CLI
uv run pytest tests/ # Run tests
uv run pre-commit run --all-files  # Lint
```

## NOTES

- Dual entry points: `vibe` (CLI), `vibe-acp` (editor protocol)
- Uses `textual` for TUI
- MCP for external tool integrations
- ACP for IDE editor integration (Zed, VS Code)
