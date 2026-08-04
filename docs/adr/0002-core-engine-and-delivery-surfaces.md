# 0002 Core Engine And Delivery Surfaces

## Decision

`vibe/core` supplies reusable private engine implementations: agent loop,
tools, LLM backends, config, sessions, skills, hooks, telemetry types, and
shared domain models. `vibe/app_server` is the harness and runtime composition
root that owns those objects for delivery surfaces.

Delivery surfaces adapt that engine:

- `vibe/cli` owns the Textual app, terminal UX, widgets, slash-command presentation, voice UI, and local interactive affordances. Model-visible shell execution crosses the app server.
- `vibe/acp` owns thin Agent Client Protocol translation and ACP-specific presentation updates over the app-server client API.
- `vibe/setup` owns first-run and onboarding flows.
- Programmatic entry points consume the app-server client API without depending on Textual, ACP internals, or a live core object.

`AppServerHost` is the passive pre-session facade for listing, reading,
deleting, trust, and session opening. `AppServerSession` is the attached facade
for turns, callbacks, resources, and live events. Textual, ACP, and
programmatic mode use these public facades rather than private app-server
modules.

## Rationale

The same engine must serve multiple clients. UI or protocol behavior should not leak into core decisions because it makes the engine harder to test, reuse, and replace.

## Agent Guidance

- Add user-interface behavior in `vibe/cli`, not `vibe/core`.
- Add protocol translation in `vibe/acp`, not `vibe/core`.
- Keep core events and models surface-neutral. Let surfaces render or translate them.
- Route every delivery surface through typed app-server requests, public events,
  and resource views; do not create a second core adapter for ACP or `-p` mode.
- Shared behavior belongs in core only when it is truly independent of the delivery surface.
- When a surface needs special behavior, prefer an adapter or subclass at that surface boundary.

## Flag To User When

- Implementing a feature requires `vibe/core` to import Textual, ACP schema objects, or setup UI code.
- A protocol-specific or UI-specific workaround is being added to a core model.
- A change makes programmatic mode depend on interactive terminal assumptions.
