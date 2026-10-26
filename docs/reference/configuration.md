# Configuration reference

This page describes configuration resolution and the app-server API.
[ADR 0005](../adr/0005-layered-configuration.md) defines the constraints;
[ADR 0002](../adr/0002-core-engine-and-delivery-surfaces.md) defines ownership.
The schema, orchestrator and adapter remain authoritative for implemented behavior.

## Current layer stack

The effective order is:

1. `DefaultConfigLayer`, materialized from `VibeConfigSchema` defaults;
2. `ModelCatalogLayer`, materialized from the cached or fetched model catalog
   when `experimental_enable_model_catalog` is on; it contributes models, which
   every layer above can override field by field, and the aliases a pin can
   name them by;
3. `GrowthbookLayer`, materialized from remote or hydrated experiment assignments;
4. the user TOML layer (`~/.vibe/config.toml`) when the `"user"` source is
   enabled, followed by the project TOML layer (a discovered trusted
   `.vibe/config.toml`) when the `"project"` source is enabled;
5. `VIBE_*` environment values;
6. session/runtime overrides;
7. the active agent profile layer (`AgentProfileLayer`); and
8. the enforced admin layer (`AdminConfigLayer`), which shadows every layer
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

- `~/.vibe/config.toml` is the default write target whenever the user source is
  enabled, including when a trusted project config is discovered: project
  discovery walks up parent directories, so in a monorepo the closest project
  file is rarely the scope the writer meant;
- project-only composition uses the project layer, creating
  `.vibe/config.toml` on first write when absent;
- composition without a persistent source uses the runtime override layer.

`active_model` uses the same write-target resolution as every other field.
When a session already has a pinned model, the app server additionally mirrors
an implicit `active_model` write into the runtime override layer; this does not
change the target of the original write. An explicit persistent target,
including one selected through `/config`, changes only that layer. A write
explicitly targeting the session override changes that override directly.
Accepted user input establishes the session model pin. Live metadata persistence
records the resolved alias as `active_model` and the provider name as
`model_name`; later host-only mutations preserve that config snapshot.

The thinking level travels with the model pick and is session-scoped the same
way, except that it lives under a model entry rather than beside it: the
app server mirrors the session's level into the runtime override layer as a
field of the active model, so a level picked in one session is not the level
another session reopens on. Session metadata records it at the same turn
boundary as the model, and records the effective level, so a model change moves
the recorded level to the model now active.

A model declared by a layer above the override — an agent profile, an admin
policy — is exempt: that layer both outranks the override and may be replaced
while the session runs, which would leave the override as the model's only and
incomplete definition. The level of such a model stays the layer's to set, and
the session neither scopes nor records it.

An `active_model` that names one of a catalog model's `aliases` is first
resolved to that model's ID. An `active_model` absent from the merged model
list is then normalized to the empty, unpinned sentinel. Default resolution
then applies: the GrowthBook routed default, then the model catalog's
recommendation, then the built-in default, each skipped when `allowed_models`
excludes it. The invalid source value is left untouched and reported as a
validation warning that names no cause.

A model entry without `provider` only overrides a model another layer defines.
When no layer defines that model, the entry is left out of the merged config and
reported as a validation warning; it applies again once the model returns.
Missing-model warnings are never suppressed while a catalog fetch is pending:
the session-start wait (VIBE-5122) is the only hold. A field write to a model
the catalog or GrowthBook layer defines stays settings-only for the same
reason — the layer redefines the model at launch, and copied identity would
keep a retired model running.

A `compaction_model` on another provider than the active model yields to the
active model: compaction rides the active model's connection, so it has no route
to a second provider. The source value is left untouched and reported as a
validation warning rather than rejected, so a model pick, including a restored
session pin, never fails on it.

Clients that let a user pick a scope (the `/config` screen) offer the default
target, the trusted project layer, and the session override layer, in that
order; each write op names its target layer explicitly.

An untrusted project layer loads empty and is skipped by the merge builder.
When the user source is enabled, implicit writes fall back to the user layer.
A user or project TOML value overrides the corresponding GrowthBook assignment.

A/B test assignments that affect session behavior are product configuration
inputs. Map them into config fields through `GrowthbookLayer`, consume the
effective `VibeConfigSchema` in app-server composition, and pass resolved values
through Runtime capabilities. Direct `ExperimentManager` branching bypasses
TOML, environment and session override precedence.

Session options such as enabled tools, disabled tools, and ephemeral MCP
servers are override-layer values. Forks and child sessions receive independent
orchestrator copies. Child-only values, such as child session logging, are
written to the copied override layer rather than persisted into the parent's
TOML.

## Settings Vibe does not use

`VibeConfigSchema.check_layer_keys` checks the keys of the user TOML, a
supplied user config, the trusted project TOML and the active agent profile
against the schema (`vibe.core.config._setting_keys`). It reports:

- a key that is not a setting, with the closest setting name when there is one;
- a retired setting, such as `include_model_info`; and
- a setting with no effect under the unified harness, such as `[tools.grep]` or
  `tools.read_file.max_read_bytes`.

A tool setting is checked against the tool's own settings model. A custom tool
reads its own settings, so `[tools.<name>]` is not checked when the tool
manager selects a custom tool for that name, including one that replaces a
builtin. When the selected variant is the builtin, for example because the
custom tool is unavailable or has a lower `selection_priority`, the builtin's
settings model applies.

By default each finding is a warning. It is logged, shown at startup, written to
stderr and the export by `vibe -p`, and returned in `validation_warnings` by
`config/read`. With `strict_config = true` in a config file, or
`VIBE_STRICT_CONFIG=1`, the config is refused with `UnusedConfigKeysError`
instead. Clients receive `invalid_params` with data `{"kind": "configuration"}`
for a session start, a session read, and a picker read without a session
(`config/read`, `agents/list`, `skills/list`). `VIBE_STRICT_CONFIG` takes a boolean,
such as `1`, `true`, `yes` or `on` to refuse and `0`, `false`, `no` or `off` to
warn; any other non-empty value is refused, because the environment layer would
otherwise drop it and leave strict mode off.

`vibe config schema` prints the JSON schema of every setting, served by
`config/schema`. It includes each builtin tool's settings under `tools`, marks
retired settings as deprecated, and sets `additionalProperties: false` so an
editor can flag unknown keys.

## App-server config boundary

`ConfigView` is a redacted public projection, not a second writable config
schema. It contains only values a client must render or apply and never contains
resolved API keys, tokens, connector credentials, or arbitrary environment
values. Clients must not infer writable paths from its shape.

The resource catalogue is defined by
[`vibe.app_server.protocol`](../../vibe/app_server/protocol.py):

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

1. validates the target session and any active rewrite reservation;
2. converts the ops into schema-aware patches and preflights the merged config;
3. persists accepted layer edits and rebuilds the effective snapshot;
4. applies the derived configuration or defers it to the next turn boundary; and
5. returns canonical runtime state and mutation status, with `runtime/updated`
   notifications for accepted changes.

`config/reload` requires an idle session. A supported model-only write may be
accepted mid-turn with pending application; other execution-setting changes
conflict while a turn is active. An accepted configuration write is therefore
distinct from its application to execution. Settings pickers retain their
idle-only product policy under [0012](../adr/0012-two-phase-slash-command-execution.md).

One TOML-layer write uses a temporary file, `fsync`, and atomic replacement.
The general orchestrator is not transactional across several target layers;
each op may name a target layer, and ops without one route to the selected
writable layer.

The public API accepts `{op, path, value, target_layer}` ops and a
client-supplied `reloadRuntime` choice. It does not expose resource revisions,
complete provenance or per-field runtime-impact metadata. Clients use explicit
layer targets rather than infer them from a redacted view.

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

Before attachment, launchers may read startup configuration for process setup.
The session-less `setup/*` API persists onboarding choices and credentials;
`config/write` serves attached sessions. The server owns keyring and
`$VIBE_HOME/.env` credential custody.

## Administrator environment variables

`VIBE_CUSTOM_CONSOLE_URL` pins the console URL for VS Code and Vibe CLI
sign-in. It supports centrally managed deployments: when set, sign-in is
prefilled with that console and redirected into its normal login flow, and the
domain can no longer be changed. The manual API-key sign-in path stays
available.

The value may be a full URL or a bare domain. Scheme-less values are upgraded
to `https://`; an explicit `http://` is kept (useful for local auth gateways);
trailing slashes are stripped. The variable name is matched case-insensitively.
An unset, empty, or invalid value means no pin, and an invalid value logs a
warning rather than blocking sign-in.

The pin fixes the browser sign-in host only. In a split-horizon deployment
whose configured browser auth base matches the pin, the configured
`browser_auth_api_base_url` is preserved (the API base otherwise derives as
`{pin}/api`), and account calls keep the CLI-reachable API base's origin.
