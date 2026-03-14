# vibe/core - Core Agent Logic

High-complexity module (99 files). Contains agent orchestration, tools, LLM backends, and plugin system.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Agent loop | `agent_loop.py` | Main orchestration (45KB) |
| Tools base | `tools/base.py` | `Tool` class |
| Tool manager | `tools/manager.py` | Tool registry |
| Built-in tools | `tools/builtins/` | bash, grep, read_file, etc. |
| MCP tools | `tools/mcp/` | MCP integration |
| LLM backends | `llm/backend/` | Mistral, Anthropic, Vertex, Generic |
| Plugins | `plugins/` | Built-in (hello, lsp) + custom |
| Skills | `skills/` | Extensibility system |
| Session | `session/` | Session loader/logger |
| Autocompletion | `autocompletion/` | File indexer, completers |

## KEY CLASSES

| Class | File | Purpose |
|-------|------|---------|
| `AgentLoop` | `agent_loop.py` | Main orchestrator |
| `Tool` | `tools/base.py` | Base tool class |
| `ToolManager` | `tools/manager.py` | Tool registry |
| `LLMBackend` | `llm/backend/base.py` | LLM provider base |
| `PluginManager` | `plugins/manager.py` | Plugin loader |
| `SkillManager` | `skills/manager.py` | Skill loader |

## CONVENTIONS

- Inherits all root conventions
- Tools: subclass `Tool`, implement `execute()`
- LLM backends: subclass `LLMBackend`, implement `complete()`
- Plugins: subclass `Plugin` from `plugins/base.py`
- Skills: directory with `SKILL.md` + `__init__.py`

## ANTI-PATTERNS

- No tool execution in `__init__` - lazy load only
- No blocking calls in LLM backends - use async
- No plugin loading failures - graceful degradation

## NOTES

- MCP tools via `mcp` package (>=1.14.0)
- LSP plugin uses `pygls` for async LanguageClient
- Session logs stored in `~/.vibe/sessions/`
