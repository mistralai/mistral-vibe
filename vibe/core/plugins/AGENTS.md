# vibe/core/plugins — Plugin System

Plugin framework supporting extensible agent behavior (13 files). Enables custom tools, middleware hooks, and builtin extensions.

## STRUCTURE

```
vibe/core/plugins/
├── __init__.py
├── base.py              # Abstract Plugin class
├── manager.py           # Plugin registry & loading (306 lines)
├── middleware.py        # Middleware chain execution (178 lines)
├── agent_loop_patch.py  # Agent integration hooks (124 lines)
├── command_plugin.py    # Command-line plugin interface (32 lines)
└── builtin/             # Built-in plugins
    ├── __init__.py
    ├── hello/           # Demo plugin (~50 LOC)
    └── lsp/             # LSP integration (~150 LOC)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Plugin base class | `base.py` | Abstract `Plugin` |
| Plugin manager | `manager.py` | Registry, loading, lifecycle |
| Middleware | `middleware.py` | Hook chain execution |
| Agent integration | `agent_loop_patch.py` | Session/tool hooks |
| LSP plugin | `builtin/lsp/` | Language server protocol |

## KEY CLASSES

| Class | File | Purpose |
|-------|------|---------|
| `Plugin` | `base.py` | Abstract base class |
| `PluginManager` | `manager.py` | Plugin registry & lifecycle |
| `CommandPlugin` | `command_plugin.py` | CLI command registration |
| `MiddlewareChain` | `middleware.py` | Hook execution pipeline |

## CONVENTIONS

- **Inherits root conventions** (Python 3.12+, no relative imports)
- **Lazy loading**: Plugins load on-demand, not in `__init__`
- **Async support**: Middleware hooks can be async
- **Error isolation**: Plugin failures don't crash agent loop
- **Version detection**: Builtin plugins auto-discovered via `builtin/__init__.py`

## ANTI-PATTERNS

- No plugin execution in `__init__` — lazy load only
- No bare exception handlers — graceful degradation required
- No blocking calls in async middleware hooks
- No direct tool registration outside manager API

## NOTES

- Builtin plugins: `hello` (demo), `lsp` (language server)
- Custom plugins: Place in `~/.vibe/plugins/` or configured paths
- Middleware hooks: `before_tool_execute`, `after_session_start`
- Agent loop patches: Injected via `agent_loop_patch.py`
