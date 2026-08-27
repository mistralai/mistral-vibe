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

The selected `SessionBackend` is the application boundary for changes to that
live runtime. Agent switches, session-limit updates, config writes, and reloads
use distinct typed backend methods after Host-side validation and persistence;
resource handlers do not reach into an implementation-specific runtime object.

## Current layer stack

The effective order is:

1. `DefaultConfigLayer`, materialized from `VibeConfigSchema` defaults;
2. `GrowthbookLayer`, materialized from remote or hydrated experiment assignments;
3. the user TOML layer (`~/.vibe/config.toml`) when the `"user"` source is
   enabled, followed by the project TOML layer (a discovered trusted
   `.vibe/config.toml`) when the `"project"` source is enabled;
4. `VIBE_*` environment values;
5. session/runtime overrides;
6. the active agent profile layer (`AgentProfileLayer`); and
7. the enforced admin layer (`AdminConfigLayer`), which shadows every layer
   below it.

`AgentProfileLayer` ships as a statically installed empty slot positioned just
below the admin layer. `AgentManager` owns its contents: it fills the slot for
the initial agent and, on a profile switch, replaces the layer in place with
the new overrides, then rebuilds the orchestrator synchronously (`rebuild`, a
`run_sync` bridge over `build`). Because the slot is replaced in place its
priority is fixed by the stack definition, so the admin layer always outranks
a profile. The effective config is the single merged result; there is no
separate profile-free "base" config.

The user and project TOML layers are installed together so a trusted project
config inherits unspecified values from the user config; per-field merge
strategies (`REPLACE` / `UNION` / `CONCAT` / `SHALLOW` / `DEEP`) decide how
overlapping fields combine, with the project layer taking priority. An untrusted
or absent project layer is skipped from the merge by the builder while the user
layer still contributes.

The default write target is selected from the installed layers:

- a discovered and trusted project layer is the default write target;
- project-only composition uses the project layer, creating
  `.vibe/config.toml` on first write when absent;
- otherwise `~/.vibe/config.toml` is selected when the user source is enabled;
- composition without a persistent source uses the runtime override layer.

An untrusted project layer loads empty and is skipped by the merge builder.
When the user source is enabled, implicit writes fall back to the user layer.
A user or project TOML value overrides the corresponding GrowthBook assignment.

The default, GrowthBook, user, project, environment, override, agent-profile,
and admin layers are all part of the default orchestrator stack. The
agent-profile layer is statically installed but ships empty; `AgentManager`
fills it in place when a profile is selected and rebuilds the orchestrator.
Code must follow the live stack rather than assume a fixed layer set, since
optional TOML layers may be absent.

A/B test assignments that affect runtime behavior are configuration inputs.
They must be mapped into config fields by `GrowthbookLayer`, then consumed from
the effective `VibeConfigSchema`. Runtime code must not branch directly on
`ExperimentManager` variants for behavior that can be represented as config,
otherwise TOML, environment, and session override precedence is bypassed.

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

- `config/read` returns the effective redacted view (a single merged config;
  no separate base view);
- `config/reload` re-reads configured sources and optionally rebuilds runtime
  state;
- `config/write` validates and persists JSON-pointer edits, applying `set` and
  `remove` ops that each optionally target a named layer;
- `config/proxy/read` and `config/proxy/write` manage the supported global
  proxy and certificate `.env` entries; and
- `config/schema` exposes the live schema used by ACP settings clients.

The proxy resource is deliberately separate from the TOML orchestrator.
`config/schema` is configuration-form metadata; it is not a list of valid
`config/write` paths and is not the public app-server protocol schema.

For `config/write`, the server:

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
- Route A/B-tested runtime behavior through `GrowthbookLayer` config mappings;
  do not read `ExperimentManager` directly when a config field can represent
  the behavior.
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
