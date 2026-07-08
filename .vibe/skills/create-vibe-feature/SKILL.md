---
name: create-vibe-feature
description: Add or modify a feature in the Mistral Vibe Python CLI while following the app architecture ADRs. Use when working in vibe/core, vibe/cli, vibe/acp, vibe/setup, built-in tools, config, sessions, skills, hooks, MCP, connectors, or other Vibe feature code.
metadata:
  display-name: Create Vibe Feature
  short-description: Guide Mistral Vibe feature work
  default-prompt: Use $create-vibe-feature to implement a Mistral Vibe feature in line with the app architecture.
---

# Create Vibe Feature

Use this skill when implementing Vibe feature work. Keep changes scoped, architecture-aware, and consistent with nearby code.

## App Areas

| Area | Role | Path |
| --- | --- | --- |
| Core engine | Agent loop, domain events, tools, LLM backends, config, sessions, skills, hooks, telemetry types, shared models | `vibe/core/` |
| Textual CLI | Interactive terminal UX, widgets, slash commands, manual shell commands, voice UI, local user affordances | `vibe/cli/` |
| ACP bridge | Agent Client Protocol session, tool, terminal, title, and content translation | `vibe/acp/` |
| Setup | First-run, auth, onboarding, trusted folders, update prompts | `vibe/setup/` |
| Tests | Unit, integration, e2e, Textual snapshots, stubs and fixtures | `tests/` |

## Architecture Routing

Before implementation, read the matching ADRs:

- Architecture principles, module boundaries, startup/runtime speed, simple changes: `docs/adr/0001-architecture-principles.md`
- Core engine vs delivery surfaces: `docs/adr/0002-core-engine-and-delivery-surfaces.md`
- Agent loop orchestration, streaming, events, cancellation, responsiveness: `docs/adr/0003-event-driven-agent-loop.md`
- Tool contracts, permissions, output, UI metadata, adapters: `docs/adr/0004-typed-permissioned-tools.md`
- Config models, layering, defaults, migrations, reloads: `docs/adr/0005-layered-configuration.md`
- Session logging, resume, rewind, transcript metadata, migrations: `docs/adr/0006-local-sessions.md`
- Skills, agents, subagents, hooks, MCP, connectors, custom tools, discovery: `docs/adr/0007-extension-mechanisms.md`
- Feature work, telemetry events, analytics properties, instrumentation verification: `docs/adr/0008-feature-instrumentation.md`

If a change fits the current code but conflicts with ADR direction, flag it to the user before implementing.

## Workflow

1. Read `README.md`, `AGENTS.md`, and the nearest relevant source files before editing.
2. Identify the owning area. Keep UI behavior in `vibe/cli`, ACP translation in `vibe/acp`, setup flow in `vibe/setup`, and reusable engine behavior in `vibe/core`.
3. Study one or two existing features with the same shape before adding new files. Match naming, model placement, port/adapters, tests, and error patterns.
4. Prefer a small change in the owning module. Add a port or abstraction only when it protects a meaningful boundary or makes replacement/testing easier.
5. For feature work, plan telemetry with `instrument-feature-analytics` before writing tracking code.
6. Keep startup and interactive latency in mind. Avoid eager imports, broad scans, and network or subprocess work unless the configured feature needs them.
7. Update docs or built-in skill guidance when the feature changes user-visible CLI behavior, config, commands, agents, persistence, or discovery.

## Consistency Checks

- Core code should not import Textual, ACP schemas, setup UI, or other delivery-surface details.
- Surface code should adapt core events/models instead of reaching into private core state.
- External data should enter through Pydantic validation, typed events, tool args/results, config models, or explicit ports.
- Tools should follow `BaseTool` with typed args, result, config, state, and permission behavior.
- Tests should mirror the source layout and use existing fixtures or `tests/stubs/Fake*` doubles.

## Verification

After Python changes, run:

```bash
uv run ruff format .
uv run ruff check --fix .
```

For behavior changes, run the narrowest relevant tests first, then broader checks if the change touches shared contracts:

```bash
uv run pytest <path-or-test>
uv run pyright
```
