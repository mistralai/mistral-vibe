# AGENTS.md

Conventions for AI agents and humans contributing to **Mistral Vibe** — a CLI coding assistant: a Rust terminal UI over a Python 3.12+ app server managed with `uv`.

Layout: Unified Harness Core and Runtime are external dependencies that execute and persist sessions; `vibe/app_server` hosts sessions and exposes product features and RPCs; `vibe/core` retains shared config, tool, permission and other utilities; `vibe/cli-rust` is the terminal UI (Rust, Ratatui), a thin client over `vibe-app-server`; `vibe/cli` holds the `vibe` launcher that execs it and the audio helpers `vibe/acp` uses; `vibe/acp` bridges to the Agent Client Protocol; `vibe/setup` holds auth and the Textual onboarding wizard behind `vibe-acp --setup`. Tests live in `tests/` with autouse fixtures in `conftest.py` and test doubles in `tests/stubs/`.

## Architecture Decisions

Before architecture-affecting changes, read [0002 Ownership Boundaries](docs/adr/0002-core-engine-and-delivery-surfaces.md) and the matching ADR below. For new agent capabilities, also read [0018](docs/adr/0018-modular-app-server-capabilities.md), including its Runtime/Core scope rule. Flag conflicts with ADR direction before implementing. When writing ADRs, follow the `write-vibe-adr` skill.

| Change area | ADR |
| --- | --- |
| Architecture principles, module boundaries, startup/runtime speed, simple changes | [0001 Architecture Principles](docs/adr/0001-architecture-principles.md) |
| Core/Runtime/app-server ownership, session hosting, adapters, or delivery clients | [0002 Ownership Boundaries](docs/adr/0002-core-engine-and-delivery-surfaces.md) |
| Tool contracts, permissions, tool output, UI metadata, or tool adapters | [0004 Typed Permissioned Tools](docs/adr/0004-typed-permissioned-tools.md) |
| Config models, layering, defaults, migrations, reloads, or runtime overrides | [0005 Layered Configuration](docs/adr/0005-layered-configuration.md) |
| Session logging, resume, rewind, transcript metadata, or migrations | [0006 Local Sessions](docs/adr/0006-local-sessions.md) |
| Skills, agents, subagents, hooks, MCP, connectors, custom tools, or discovery | [0007 Extension Mechanisms](docs/adr/0007-extension-mechanisms.md) |
| Adding or changing analytics instrumentation, telemetry events, or event properties | [0008 Feature Instrumentation](docs/adr/0008-feature-instrumentation.md) |
| Client protocol, streaming, cancellation, projections, ordering, callbacks, or transports | [0009 App Server Client Boundary](docs/adr/0009-app-server-boundary.md) |
| Textual `Content` rendering, styled text, markup parsing, or theme variables in the `vibe-acp --setup` onboarding wizard | [0010 Textual Content Rendering](docs/adr/0010-textual-content-rendering.md) |
| Slash commands running while busy, side-channel commands, or idle-only settings persistence | [0012 Slash Commands While Busy](docs/adr/0012-two-phase-slash-command-execution.md) |
| Queued-prompt selection/edit mode, copy-on-write consumed edits, or promotion-race widget-identity tracking | [0013 Queue Selection and Edit Mode](docs/adr/0013-queue-edit-mode.md) |
| Parsing backend responses, connector/MCP bootstrap fields, or any client/backend wire-contract change that must tolerate self-hosted version skew | [0014 Backend Contract Compatibility](docs/adr/0014-backend-contract-compatibility.md) |
| Outbound TLS connections, certificate roots, or `enable_system_trust_store` | [0015 Outbound TLS Trust Policy](docs/adr/0015-outbound-tls-trust-policy.md) |
| Rust CLI delivery surface, thin-client boundary, unidirectional state, or bounded-resource rules | [0016 Rust CLI Delivery Surface](docs/adr/0016-rust-cli-delivery-surface.md) |
| Narration playback, TTS, audio output device following, or the `AudioPlayer`/`DeviceFollower`/`NarrationController` classes | [0017 Narration Playback in the ACP Subprocess](docs/adr/0017-narration-playback-in-subprocess.md) |
| Adding agent behavior, custom tools, built-in plugins/skills, RPC families, or feature-module dependencies | [0018 Modular App-Server Capabilities](docs/adr/0018-modular-app-server-capabilities.md) |

Superseded records: [0003](docs/adr/0003-event-driven-agent-loop.md) → 0009; [0011](docs/adr/0011-unified-harness-backend.md) → 0002. Their paths remain for existing links.

## Terminal UI changes

The terminal UI is `vibe/cli-rust`. Follow `vibe/cli-rust/AGENTS.md` and the `vibe-rust-tui` skill, and cover every user-visible change with a `client-e2e` scenario and golden (see `client-e2e/AGENTS.md`).

- Put server-owned behavior (session, config and persistence, tools, network; see the ownership table in ADR 0002) behind an app-server method or notification so the TUI and `vibe-acp` only render it. Keep presentation state (widgets, layout, focus, selection, editing) in the client.
- The Textual onboarding wizard in `vibe/setup/onboarding` only serves `vibe-acp --setup`; do not add terminal UI features there.

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
- Keep the builtin Vibe Skill (`vibe/plugins/builtins/vibe/skills/vibe/SKILL.md`) up-to-date. It documents the CLI's features, such as args, flags, config options and persistence, commands, built-in agents, file discovery logic.
- When adding or changing Vibe config fields, check whether Settings Manager's Vibe Code managed-config UI and Le Chat Web's backend allowlist/schema must be updated. If no update is needed, say so explicitly in the change notes.
