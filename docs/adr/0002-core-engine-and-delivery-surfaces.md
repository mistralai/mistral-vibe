# 0002 Core Engine And Delivery Surfaces

## Decision

`vibe/core` owns the agent engine: agent loop, tools, LLM backends, config, sessions, skills, hooks, telemetry types, and shared domain models.

Delivery surfaces adapt that engine:

- `vibe/cli` owns the Textual app, terminal UX, widgets, slash-command handling, manual shell commands, voice UI, and local interactive affordances.
- `vibe/acp` owns Agent Client Protocol translation and ACP-specific tool/session updates.
- `vibe/setup` owns first-run and onboarding flows.
- Programmatic entry points should call core orchestration without depending on Textual or ACP internals.

## Rationale

The same engine must serve multiple clients. UI or protocol behavior should not leak into core decisions because it makes the engine harder to test, reuse, and replace.

## Agent Guidance

- Add user-interface behavior in `vibe/cli`, not `vibe/core`.
- Add protocol translation in `vibe/acp`, not `vibe/core`.
- Keep core events and models surface-neutral. Let surfaces render or translate them.
- Shared behavior belongs in core only when it is truly independent of the delivery surface.
- When a surface needs special behavior, prefer an adapter or subclass at that surface boundary.

## Flag To User When

- Implementing a feature requires `vibe/core` to import Textual, ACP schema objects, or setup UI code.
- A protocol-specific or UI-specific workaround is being added to a core model.
- A change makes programmatic mode depend on interactive terminal assumptions.
