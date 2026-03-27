# vibe/ — Main Package

Root-level Python package containing CLI, ACP server, agent core, and setup modules. ~140 files excluding `__pycache__/`.

## STRUCTURE

```
vibe/
├── __init__.py          # Version export
├── cli/                 # CLI entry + Textual TUI (7 files)
│   ├── entrypoint.py    # Main `vibe` command
│   ├── cli.py           # Argument parsing
│   └── textual_ui/      # Textual-based UI components
├── acp/                 # Agent Client Protocol server
│   └── entrypoint.py    # `vibe-acp` command (93 lines)
├── core/                # Agent loop, tools, LLMs, plugins
│   ├── agent_loop.py    # Main orchestration (~45KB)
│   ├── tools/           # Built-in + MCP tool system
│   ├── llm/backend/     # Provider backends
│   ├── plugins/         # Plugin system
│   └── skills/          # Extensibility framework
├── setup/               # Onboarding & trust folder dialogs
└── __version__.py       # Version from git tags
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| CLI entry | `cli/entrypoint.py` | `vibe` command (163 lines) |
| ACP server | `acp/entrypoint.py` | Editor protocol (93 lines) |
| Agent loop | `core/agent_loop.py` | Main orchestration |
| Tools system | `core/tools/` | Built-in + MCP tools |
| LLM backends | `core/llm/backend/` | Mistral, Anthropic, Vertex |
| Plugins | `core/plugins/` | hello, lsp built-ins |

## CONVENTIONS

- Inherits all root conventions (Python 3.12+, no relative imports)
- **No `__init__.py` execution** — lazy load only
- **Async-first** in LLM backends and agent loop
- **Version via hatch-vcs** — derived from git tags

## ANTI-PATTERNS

- No tool execution in `__init__`
- No blocking calls in async contexts
- No bare exception handlers
