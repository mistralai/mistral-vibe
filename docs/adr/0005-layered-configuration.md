# 0005 Layered Configuration

## Decision

Configuration is layered, validated as a coherent snapshot, and model-driven.
`VibeConfigSchema` is the canonical effective server schema. Every field has an
explicit merge strategy, and external data is parsed through Pydantic rather
than ad-hoc dictionary walks.

For an attached session, the app server owns the `ConfigOrchestrator`, effective
configuration, persistence target, reloads, and config-derived runtime state.
Textual, ACP, and programmatic clients use typed app-server resources. They do
not read or edit `config.toml`, receive the orchestrator, or mutate a live config
object.

## Current layer stack

The effective order is:

1. `DefaultConfigLayer`, materialized from `VibeConfigSchema` defaults;
2. one selected TOML layer: trusted project config or user config;
3. `GrowthbookLayer`, materialized from remote or hydrated experiment assignments;
4. `VIBE_*` environment values;
5. session/runtime overrides; and
6. the active agent profile overlay, currently applied by `AgentManager` after
   the orchestrator result.

The selected TOML layer is also the default persistence target:

- a discovered and trusted project `.vibe/config.toml` is selected;
- otherwise `~/.vibe/config.toml` is selected when user config is enabled;
- project-only composition may create the project file; and
- composition without a persistent source uses an ephemeral override layer.

User and project TOML are not currently installed together. A selected project
file does not inherit unspecified values from the user file. The default layer
is active; discovered and agent-profile layer classes exist but are not yet
part of the default orchestrator stack. Code must follow the live stack rather
than assume those layers are active.

Session options such as enabled tools, disabled tools, and ephemeral MCP
servers are override-layer values. Forks and child sessions receive independent
orchestrator copies. Child-only values, such as child session logging, are
written to the copied override layer rather than persisted into the parent's
TOML.

## App-server config boundary

`ConfigView` is a redacted public projection, not a second writable config
schema. It contains only values a client must render or apply and never contains
resolved API keys, tokens, connector credentials, or arbitrary environment
values. Clients must not infer writable paths from its shape.

The current resource methods are defined by `vibe.app_server.protocol`:

- `config/read` returns effective and base redacted views;
- `config/reload` re-reads configured sources and optionally rebuilds runtime
  state;
- `config/patch` validates and persists JSON-pointer edits, applying `set` and
  `remove` ops that each optionally target a named layer;
- `config/thinking/write` updates the active model's thinking level;
- `config/proxy/read` and `config/proxy/write` manage the supported global
  proxy and certificate `.env` entries; and
- `config/schema` exposes the live schema used by ACP settings clients.

The proxy resource is deliberately separate from the TOML orchestrator.
`config/schema` is configuration-form metadata; it is not a list of valid
`config/patch` paths and is not the public app-server protocol schema.

For `config/patch`, the server:

1. requires the session to be idle;
2. converts all ops into one schema-aware patch;
3. validates the prospective merged config;
4. writes the selected layer once;
5. replaces the effective config with a newly validated snapshot;
6. invalidates or rebuilds derived runtime state; and
7. returns a canonical `RuntimeSnapshot` and emits `runtime/updated`.

One TOML-layer write uses a temporary file, `fsync`, and atomic replacement.
The general orchestrator is not transactional across several target layers;
each op may name a target layer, and ops without one route to the selected
writable layer.

The current public API does not expose an explicit user/project write scope,
resource revision, complete provenance, or per-field runtime-impact metadata.
It accepts generic `{op, path, value, target_layer}` ops and a client-supplied
`reloadRuntime` choice. Do not emulate missing config primitives with shadow
state in `vibe.app_server`; add them to the configuration substrate before
projecting them through the public resource.

## Client-local application

Persistence ownership and runtime application are separate:

- model, agent, permission, tool, MCP, connector, hook, workspace, and session
  settings are applied by the server;
- committed theme, clipboard, terminal-notification, and audio settings are
  applied from accepted server state; temporary presentation previews may
  remain local; and
- microphone and speaker enumeration, recording, playback, and device failures
  remain client-local state.

Audio managers consume the redacted public view. They do not import the private
config schema. A local hardware failure may produce a client warning, but it
must not silently mutate server config.

Before a session is attached, CLI and ACP launchers still load dotenv values,
create initial files, run onboarding, and read startup config for process-level
setup. This is bootstrap staging, not a second attached runtime. After
attachment, live config reads, writes, reloads, trust decisions, and derived
resource refreshes are server operations.

## Rationale

Vibe must combine defaults, persisted preferences, trusted project policy,
environment values, session options, agents, tools, MCP, connectors, and other
extensions without making delivery surfaces understand persistence.
Schema-aware layering provides deterministic merge behavior and one validated
effective snapshot. App-server ownership prevents the UI, ACP, and runtime from
becoming competing sources of truth.

## Agent Guidance

- Add fields to the relevant Pydantic config model with explicit defaults,
  validation, and merge metadata.
- Preserve deterministic layer ordering and keep session overrides separate
  from persisted defaults.
- Mutate attached-session config through app-server resources or server-owned
  orchestrator calls, never from Textual.
- Return canonical server state after a mutation; clients replace their cache
  instead of optimistically merging arbitrary dictionaries.
- Keep redaction in the server projector. Public views expose only what the
  client needs.
- Keep config migration and persisted-format compatibility near config models
  and layers.
- Avoid loading optional integrations during startup unless active config
  requires them.

## Flag To User When

- A feature needs hidden global state instead of config or session state.
- A config value is parsed manually or persisted from more than one owner.
- A new public write needs explicit scope, provenance, conflict detection, or
  runtime-impact semantics that the current substrate does not provide.
- A client needs a private config object, TOML path, secret, or orchestrator.
- A new config path would make startup slower for users who do not use the
  feature.
