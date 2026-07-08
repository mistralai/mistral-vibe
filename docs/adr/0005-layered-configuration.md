# 0005 Layered Configuration

## Decision

Configuration is layered, validated, and model-driven. Defaults, user config, project config, harness files, agents, tools, MCP servers, connectors, experiments, and runtime overrides merge through schema-aware config models.

External config data should be parsed with Pydantic validation and explicit merge rules rather than ad-hoc dictionary walks.

## Rationale

Vibe must be configurable at user, project, agent, and integration levels. Schema-driven layering keeps behavior predictable and lets UI, programmatic mode, and tests reason about the same effective config.

## Agent Guidance

- Add config fields to the relevant Pydantic model with explicit defaults and validation.
- Preserve deterministic layer precedence and merge behavior.
- Keep reloadable config separate from state that should live only for a session.
- Avoid making startup load optional integrations unless the config requires them.
- Keep config migration and backward compatibility near the config models.

## Flag To User When

- A feature needs hidden global state instead of config or session state.
- A config value is parsed manually in multiple places.
- A new config path would make startup slower for users who do not use the feature.
