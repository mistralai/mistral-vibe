# 0007 Extension Mechanisms

## Decision

Vibe extends through explicit mechanisms: agents, subagents, skills, hooks, MCP servers, connectors, custom tools, and config layers.

Extensions should be discoverable, filterable, typed where possible, and isolated from core startup and core control flow unless actively configured.

For attached sessions, the app server owns extension discovery results,
lifecycle, authentication state, process cleanup, and public projections.
Clients use typed agent, skill, MCP, connector, and tool resource methods; hook
state is projected through runtime diagnostics and events. Clients do not
receive registries or managers.

Subagents are server-owned child sessions with independent IDs and public
projections. The parent timeline links to the child through a subagent effect;
the client never constructs or stores a child `AgentLoop`.

Filesystem plugin packages are an optional backend capability rather than a
required Vibe extension mechanism. The app server may expose Host APIs for
installation, mounts, diagnostics, and reload, while discovery, resolution,
execution, and session restore belong to a backend that advertises plugin
support. Backends without that capability do not translate plugin packages into
their native agents, skills, hooks, MCP servers, connectors, or tools.

## Rationale

Extension mechanisms let users customize Vibe without editing core code. Isolation keeps third-party or local project behavior from destabilizing the default experience.

## Agent Guidance

- Prefer an existing extension mechanism before adding a new one.
- Keep discovery deterministic and cheap; defer expensive integration work until needed.
- Reserve built-in names and avoid silently overriding built-ins with local extensions.
- Report configuration issues without crashing the whole app when safe to continue.
- Keep hooks and external processes bounded by timeouts and typed invocation/response models.
- Return canonical public resource views after mutations and refresh client
  state through app-server notifications.
- Reject plugin Host operations when the active backend does not support them.

## Flag To User When

- A feature adds a new extension path instead of using skills, agents, hooks, MCP, connectors, tools, or config.
- Extension discovery would run expensive work during startup.
- Local project behavior can override built-ins without an explicit rule.
- A delivery surface needs separate extension discovery, MCP, connector, hook,
  or subagent lifecycle logic.
- A plugin package is being partially emulated through an unsupported backend.
