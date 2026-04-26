# vibe/core/plugins — Plugin System

Plugin framework supporting extensible agent behavior (16 files). Enables custom tools, middleware hooks, and builtin extensions with enterprise-grade resilience.

## STRUCTURE

```
vibe/core/plugins/
├── __init__.py
├── base.py              # Abstract Plugin class, PluginMetadata
├── manager.py           # Plugin registry & loading (~350 lines)
├── middleware.py        # Middleware chain execution (~200 lines)
├── agent_loop_patch.py  # Agent integration hooks (124 lines)
├── command_plugin.py    # Command-line plugin interface (32 lines)
├── extension_points.py # pluggy hook specifications (NEW)
├── resilience.py       # Circuit breaker via pybreaker (NEW)
└── builtin/             # Built-in plugins
    ├── __init__.py
    ├── hello/           # Demo plugin (~50 LOC)
    └── lsp/             # LSP integration (~200 LOC, priority=50)
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
| `VibePlugin` | `base.py` | Abstract base class |
| `PluginManager` | `manager.py` | Plugin registry & lifecycle with pluggy |
| `PluginMetadata` | `base.py` | Plugin description (name, version, priority, tags) |
| `PluginContext` | `base.py` | Injected context for plugins |
| `ToolEventPlugin` | `base.py` | Hook into tool calls/results |
| `CommandPlugin` | `command_plugin.py` | CLI command registration |
| `HookSpecs` | `extension_points.py` | pluggy hook specifications |
| `PluginCircuitListener` | `resilience.py` | Circuit breaker state logging |

## CONVENTIONS

- **Inherits root conventions** (Python 3.12+, no relative imports)
- **Lazy loading**: Plugins load on-demand, not in `__init__`
- **Async support**: Middleware hooks can be async
- **Error isolation**: Plugin failures don't crash agent loop
- **Circuit breaker**: Uses pybreaker for resilience (3 failures threshold)
- **Priority ordering**: Plugins execute in priority order (0-200+)
- **pluggy integration**: Uses pluggy for formal extension points
- **Timeout protection**: All plugin operations have timeouts

## ANTI-PATTERNS

- No plugin execution in `__init__` — lazy load only
- No bare exception handlers — graceful degradation required
- No blocking calls in async middleware hooks
- No direct tool registration outside manager API

## RESILIENCE FEATURES

### Circuit Breaker (pybreaker)
- Automatically opens after 3 consecutive failures
- Recovery timeout: 30 seconds
- Excludes KeyboardInterrupt and CancelledError
- Logs all state transitions

### Timeout Protection
| Operation | Default Timeout | Config |
|-----------|----------------|-------|
| setup() | 30s | plugin_setup_timeout_sec |
| teardown() | 10s | plugin_teardown_timeout_sec |
| hook calls | 60s | plugin_call_timeout_sec |

### Priority System
| Range | Purpose |
|-------|---------|
| 0-49 | Critical system plugins |
| 50-99 | High-priority middleware (LSP: 50) |
| 100 | Default (hello plugin) |
| 150-199 | Lower priority |
| 200+ | Delayed execution |

## ENTRY POINTS

Third-party plugins can register via pyproject.toml:
```toml
[project.entry-points."vibe.plugins"]
myplugin = "mypackage.plugin:MyPlugin"
```

## NOTES

- Builtin plugins: `hello` (demo, priority=100), `lsp` (language server, priority=50)
- Custom plugins: Place in `~/.vibe/plugins/` or configured paths
- Extension points: `on_tool_call`, `on_tool_result`, `register_commands`, `get_tools`
- pluggy integration via HookSpecs class in extension_points.py
