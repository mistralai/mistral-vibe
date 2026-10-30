# 0019 Process Services

## Decision

State one OS process shares across all its channels and sessions lives on a
service held by `ProcessServices` (`vibe/app_server/_process_services.py`),
never in a module global and never on `HarnessProcess`.

- An entry point (`stdio`, `local`) builds `ProcessServices` once with
  `ProcessServices.create()`, passes it to every `HarnessProcess` it builds,
  and closes it when it stops. A multiplexed server builds one `HarnessProcess`
  per channel; they all get the same services.
- The parameter is required: no class builds its own services as a fallback.
  A component takes the one service it uses, not the whole container
  (ADR 0018).

A remote value refreshed for everyone (the org config first; the connector
catalog, experiments and the model catalog next) follows one path:

1. **Held by a service** in a `RemoteCache` (`vibe/core/utils/remote_cache.py`):
   served at once, refreshed in the background once old, one fetch per key,
   backoff after a failure, a copy on disk shared across processes.
2. **Followed by each session** with a `RemoteFollower`
   (`_remote_follower.py`). It only tells whether the value held differs from
   the one the session's config was last built from. It applies nothing.
3. **Committed at the session's turn boundary**, through one step
   (`DeferredConfiguration.commit`) that changes nothing the session reads
   until Core runs it. The value is loaded into a copy of the session's config
   as a config input (the org config into the copy's admin layer, with the
   mode it leaves; a value that is not config, like the connector catalog,
   feeds the step that resolves it). What that copy derives is pushed to Core,
   and only then is it installed on the shared config, recorded on the
   context, and published to the user.
   - Refused, by the preflight or by Core: nothing was installed, so there is
     nothing to undo. The refusal is recorded on the context, so no session on
     it (`/clear`, rewind) tries the value again until it changes or `/reload`.
   - Displaced by a turn that started first: nothing was installed either, and
     the value stays due for that turn's boundary.
4. **Followed by everything else**: once Core runs the value, MCP servers and
   connectors follow the config (`_converge_integrations`), at the boundary or
   before a reserved turn is promoted. A failure there leaves them due.

A running turn keeps the config it started with. `/reload` reloads the local
files, refreshes the remote values, then converges at once.

## Rationale

A module global is shared by every channel but hides its lifetime and leaks
between tests; state on `HarnessProcess` is per channel, so a multiplexed
server fetched and cached the same remote value once per connection. One
container built at the entry point makes both explicit: what is shared, and
who creates and closes it.

Applying each remote value by writing it into the shared config first meant a
hand-written rollback for every failure and every interleaving with turns,
`/clear`, worktree moves, MCP and the other values. Building it on a copy and
installing it only once Core runs it leaves nothing to roll back.

## Agent Guidance

- Add a process-wide service as a field of `ProcessServices` and in
  `create()`/`close()`; reach it through the context or the component that
  needs it.
- Add a remote value as: a service holding a `RemoteCache`, a
  `RemoteFollower` on the session, and a loader called from
  `_load_remote_values` that builds the value into a copy of the session's
  config and commits it through `DeferredConfiguration.commit`
  (`_commit_org_config` is the model). Never write the shared config before
  the push lands, and keep nothing to restore. Record decided and refused
  values on the context, not on the session, so `/clear` and rewind share
  them. Do not reconfigure MCP or connectors from the loader:
  `_converge_integrations` does.
- In tests, take the `process_services` fixture (with `org_server` for the
  managed-config endpoint) instead of patching module functions, and cross a
  boundary with `_settle_configuration`.

## Flag To User When

- A change needs process-wide state outside `ProcessServices`.
- A service must be closed before a channel's sessions, or depends on one
  channel's state.
- A remote value must reach a turn already running.
