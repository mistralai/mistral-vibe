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
| `PluginManager` | class | `vibe/core/plugins/manager.py` |
| `VibePlugin` | class | `vibe/core/plugins/base.py` |

## DEPENDENCIES

Key external libraries:
- **pybreaker** (>=1.4.0) - Circuit breaker for plugin resilience
- **pluggy** (>=1.0.0) - Extension point system (like pytest)
- **pygls** (>=2.1.1) - LSP client integration
- **mcp** (>=1.14.0) - Model Context Protocol

  - title: "Safe File Reading"
    description: >
      When reading files from disk, prefer the helpers in `vibe.core.utils.io` over raw
      `Path.read_text()`, `Path.read_bytes().decode()`, or `open()` calls:
      - `read_safe(path)` — synchronous read with automatic encoding detection.
      - `read_safe_async(path)` — async equivalent (anyio-based).
      - `decode_safe(raw)` — decode an already-read `bytes` object.
      These functions try UTF-8 first, then BOM detection, the locale encoding, and
      `charset_normalizer` (lazily, only when cheaper candidates fail). They return a
      `ReadSafeResult(text, encoding)` so callers always get valid `str` output without
      having to handle encoding errors manually.
      Use `raise_on_error=True` only when the caller must distinguish corrupt files from
      valid ones; the default (`False`) replaces undecodable bytes with U+FFFD.

  - title: "Imports in Cursor (no Pylance)"
    description: >
      Cursor's built-in Pyright does not offer the "Add import" quick fix (Ctrl+.). To add a missing import:
      - Use the workspace snippets: type the prefix (e.g. acpschema, acphelpers, vibetypes, vibeconfig) and accept the suggestion to insert the import line, then change the symbol name.
      - Or ask Cursor: select the undefined symbol, then Cmd+K and request "Add the missing import for <symbol>".
      - Or copy the import from an existing file in the repo (e.g. acp.schema, acp.helpers, vibe.core.*).

  - title: "Keep Builtin Vibe Skill Up-to-Date"
    description: >
      The file `vibe/core/skills/builtins/vibe.py` is the builtin self-awareness skill.
      It documents the CLI's features for the model: config.toml fields, CLI parameters, slash
      commands, agents, skills, tools, VIBE_HOME structure, and environment variables.
      When you change any of the following, update `vibe/core/skills/builtins/vibe.py`
      to reflect the new behavior:
      - CLI arguments or flags (vibe/cli/entrypoint.py)
      - config.toml fields or defaults (vibe/core/config/_settings.py)
      - Slash commands (vibe/cli/commands.py)
      - Built-in agents (vibe/core/agents/)
      - VIBE_HOME directory layout or paths (vibe/core/paths/)
      - Skill, tool, or agent discovery logic
      - Environment variables
      If in doubt, read the skill file and check whether your change makes any section stale.

  - title: "No Docstrings in Tests"
    description: >
      Do not add docstrings to test functions, test methods, or test classes.
      Test names should be descriptive enough to convey intent (e.g.,
      `test_create_user_returns_403_when_unauthorized`). Docstrings in tests add
      noise, duplicate the function name, and can suppress pytest's default output
      (pytest displays the docstring instead of the node id when one is present).
      Use inline comments sparingly for non-obvious setup or assertions instead.
## NOTES

- Dual entry points: `vibe` (CLI), `vibe-acp` (editor protocol)
- Uses `textual` for TUI
- MCP for external tool integrations
- ACP for IDE editor integration (Zed, VS Code)
- Plugin resilience via pybreaker circuit breaker
