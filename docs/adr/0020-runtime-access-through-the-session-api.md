# 0020 Runtime Access Through the Session API

## Decision

The app server reaches a Session's runtime through the Session API
(`app_server/session/*`, models in `mistralai.vibe.harness.app_server.session`),
so the local runtime can be replaced by the target runtime without changing
clients. Clients keep the app server's own routes; only the app server to
runtime connection moves. Ownership stays as in
[0002](0002-core-engine-and-delivery-surfaces.md).

- **Path per Session.** The experimental Session API path is chosen when a
  Session opens: `VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API`, then
  `experimental_local_runtime_session_api`, which an experiment can also set
  (see [0005](0005-layered-configuration.md)). The value is pinned in memory for
  that Session and never saved, so turning the flag off moves every Session back
  to the direct path at its next open. `meta.json` records `runtime_path`.
- **Direct calls are gaps.** On that path, a direct call to a runtime object is
  allowed only where the Session API has no procedure or field for the request.
  Each one is temporary and is removed when the procedure or field exists.
- **No product extension point.** Vibe-only data does not become a Session API
  extension. The app server keeps its own per-Session data in `meta.json`,
  recomputes what it can from history, and sends data that belongs to a history
  entry as optional annotations on the request that creates the entry. The
  local runtime's `vibe.*` extension items are a stopgap for the same data.
- **Vibe work stays in the app server.** The prompt queue, titles, Smart
  Approve, approval text, rewind's file restore and worktrees are app-server
  responsibilities, so they survive the runtime swap.

## Rationale

The target runtime serves any product and only the Session API. Code that
reaches into local runtime objects, or depends on Vibe-only runtime behavior,
would block the swap or move Vibe product logic into a shared runtime. Pinning
the path per Session keeps a Session consistent while it is open; not saving it
keeps a flag rollback from leaving saved Sessions bound to a broken path.

## Agent Guidance

- Send new Session work through `SessionApiCommands` and the Session API reads
  in `_session_api_path.py`. Never add a direct runtime call for something the
  Session API already covers.
- Keep both paths working: the backend contract suite runs every test on the
  direct and Session API paths.
- Read the Session's pinned value, never the flag, after the Session opens.
- Translate Session API errors back to the runtime errors the adapter maps in
  `_session_api_commands.py`, rather than branching on the path elsewhere.
- After a change to the Session API models, refresh the local runtime's copy
  with its refresh script; never edit the copy by hand.

## Flag To User When

- A feature needs a new direct runtime call, a new `vibe.*` extension item, or a
  Session API change.
- A change makes the two paths behave differently for a client.
- Work would move Vibe product behavior into a runtime.
