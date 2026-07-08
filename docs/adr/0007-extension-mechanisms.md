# 0007 Extension Mechanisms

## Decision

Vibe extends through explicit mechanisms: agents, subagents, skills, hooks, MCP servers, connectors, custom tools, and config layers.

Extensions should be discoverable, filterable, typed where possible, and isolated from core startup and core control flow unless actively configured.

## Rationale

Extension mechanisms let users customize Vibe without editing core code. Isolation keeps third-party or local project behavior from destabilizing the default experience.

## Agent Guidance

- Prefer an existing extension mechanism before adding a new one.
- Keep discovery deterministic and cheap; defer expensive integration work until needed.
- Reserve built-in names and avoid silently overriding built-ins with local extensions.
- Report configuration issues without crashing the whole app when safe to continue.
- Keep hooks and external processes bounded by timeouts and typed invocation/response models.

## Flag To User When

- A feature adds a new extension path instead of using skills, agents, hooks, MCP, connectors, tools, or config.
- Extension discovery would run expensive work during startup.
- Local project behavior can override built-ins without an explicit rule.
