# AGENTS.md

Conventions for AI agents and humans contributing to **Mistral Vibe** — a Python 3.12+ CLI coding assistant managed with `uv`.

Layout: `vibe/core` is the engine (agent loop, tools, LLM backends, config); `vibe/cli` is the Textual TUI; `vibe/acp` bridges to the Agent Client Protocol; `vibe/setup` runs first-run wizards. Tests live in `tests/` with autouse fixtures in `conftest.py` and test doubles in `tests/stubs/`.

## Architecture Decisions

Before architecture-affecting changes, read the matching ADR. If a change fits the current code but conflicts with ADR direction, flag it to the user before implementing. When creating or editing an ADR, follow the `write-vibe-adr` skill.

When creating or editing an ADR, follow the `write-vibe-adr` skill and keep the standard sections.

| Change area | ADR |
| --- | --- |
| Architecture principles, module boundaries, startup/runtime speed, simple changes | [0001 Architecture Principles](docs/adr/0001-architecture-principles.md) |
| Core engine, Textual CLI, ACP, setup, or programmatic surfaces | [0002 Core Engine And Delivery Surfaces](docs/adr/0002-core-engine-and-delivery-surfaces.md) |
| Agent loop orchestration, streaming, typed events, cancellation, or responsiveness | [0003 Event Driven Agent Loop](docs/adr/0003-event-driven-agent-loop.md) |
| Tool contracts, permissions, tool output, UI metadata, or tool adapters | [0004 Typed Permissioned Tools](docs/adr/0004-typed-permissioned-tools.md) |
| Config models, layering, defaults, migrations, reloads, or runtime overrides | [0005 Layered Configuration](docs/adr/0005-layered-configuration.md) |
| Session logging, resume, rewind, transcript metadata, or migrations | [0006 Local Sessions](docs/adr/0006-local-sessions.md) |
| Skills, agents, subagents, hooks, MCP, connectors, custom tools, or discovery | [0007 Extension Mechanisms](docs/adr/0007-extension-mechanisms.md) |
| Adding or changing analytics instrumentation, telemetry events, or event properties | [0008 Feature Instrumentation](docs/adr/0008-feature-instrumentation.md) |
| App-server ownership, RPCs, lifecycle, projections, effects, callbacks, client tools, or delivery adapters | [0009 App Server Boundary](docs/adr/0009-app-server-boundary.md) |
| Textual `Content` rendering, styled text, markup parsing, or theme variables in widgets | [0010 Textual Content Rendering](docs/adr/0010-textual-content-rendering.md) |
| App-server session backend interfaces, adapters, or runtime ownership | [0011 App Server Session Backends](docs/adr/0011-unified-harness-backend.md) |
| Slash commands running while busy, side-channel commands, `QueuedItemKind.COMMAND`, or config persistence deferral via main queue | [0012 Two-Phase Slash Command Execution](docs/adr/0012-two-phase-slash-command-execution.md) |
| Queued-message selection/edit mode, copy-on-write consumed edits, or drain-race widget-identity tracking | [0013 Queue Selection and Edit Mode](docs/adr/0013-queue-edit-mode.md) |

## Commands

Always go through `uv` — never invoke bare `python` or `pip`.

- `uv run vibe` / `uv run vibe-acp` — the two entry points.
- `uv run pytest` — full suite (parallel via `pytest-xdist`).
- `uv run pyright` — strict type check.
- `uv run ruff check --fix .` and `uv run ruff format .` — run both after every code change and report the files modified.
- `uv run pre-commit run --all-files` — full lint pass. Install once with `uv tool install pre-commit && uv run pre-commit install`.
- Useful uv basics: `uv sync --all-extras`, `uv add <pkg>`, `uv remove <pkg>`.

## Project layout & module conventions

- `__init__.py` exposes the public API via an explicit `__all__`.
- Private modules are prefixed with `_` (e.g. `_settings.py`, `_config.py`).
- Pydantic config models live in `models.py`; shared config defaults live in `_defaults.py`; the effective config schema lives in `vibe_schema.py`.
- Abstract interfaces use the `_port.py` suffix (hexagonal-style ports).
- Tests mirror the source layout: a test lives in the directory mirroring its source module's package. Tests for a `vibe/core` subpackage go under the matching `tests/core/<subpackage>/` (e.g. `vibe/core/utils/retry.py` → `tests/core/utils/test_retry.py`); tests for modules that sit directly in `vibe/core/` (e.g. `loop.py`, `types.py`) stay flat in `tests/core/`. No `__init__.py` is needed in test subdirectories — pytest runs in `--import-mode=importlib`. Test doubles in `tests/stubs/` are named `Fake*`.

## Autoimprovement

- When a change request can be generalized as a rule, prefer adding it to the relevant `.agents/skills/` SKILL.md rather than appending to AGENTS.md. AGENTS.md should stay small and universal; domain-specific conventions belong in skills so they load only when needed. Create a new skill when no existing one fits.
- Suggest updates to the README.md file according to feature changes or additions.
- Keep the builtin Vibe Skill (`vibe/core/skills/builtins/vibe.py`) up-to-date. It documents the CLI's features, such as args, flags, config options and persistence, commands, built-in agents, file discovery logic.
- When adding or changing Vibe config fields, check whether Settings Manager's Vibe Code managed-config UI and Le Chat Web's backend allowlist/schema must be updated. If no update is needed, say so explicitly in the change notes.
