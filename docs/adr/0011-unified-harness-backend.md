# 0011 App Server Session Backends

## Decision

The app server accesses session implementations through two interfaces in
`vibe.app_server._session_backend_port`.

`SessionBackendHost` owns the process-level session catalogue and lifecycle. It
starts and resumes sessions, selects the latest resumable session, lists and
reads stored sessions, creates forks, tracks live backends, and shuts them down
with the process. Fork belongs to the Host because it creates and registers a
new session identity, even though its implementation reads a source session.

A Host also reports its `harness_kind`: `python` for the legacy `AgentLoop`
adapter and `rust` for the Unified Harness. This is backend identity, not a
capability, so it stays on the common Host interface. The app server records
the kind with the session identity when a session is created. Process
composition selects which Host an app-server instance receives; backend
selection never reaches the wire protocol.

During the dual-backend migration, the invocation selects one Host for every
new, resume, fork, and child-session open. There is no shared catalogue or
persisted backend discriminator. Continue-latest and list remain scoped to the
selected backend, while an explicit session ID is the cross-backend migration
entry point.

Before binding, restoring, or importing an explicit session ID, either backend
acquires the same cross-harness operating-system lease for that ID. The
selected backend restores its private store when present. If only the other
backend has a store, the selected backend classifies the source and may import
its versioned committed-history export only at a quiescent boundary.
Recoverable unfinished work and invalid source data produce typed failures;
neither case changes the selected backend.

Import creates an independent target representation and records immutable
source provenance. A Legacy-to-Unified import mints a new target session
identity. Import never restores the other backend's checkpoint, treats a public
projection as execution state, deletes the source, or automatically merges
later divergence.

`SessionBackend` represents one bound live session. It owns public reads,
atomic event subscriptions, typed runtime configuration mutations, turn
control, context injection, callbacks, compaction, and runtime shutdown. Every
operation that reads or mutates live session state goes through the bound
backend or a narrow backend capability interface.

The app server owns transport, request validation, and application-specific
Host APIs. A backend owns its complete session state and execution lifecycle.
Neither side reaches into the other's private implementation state.

Backend failures cross the port as `SessionBackendError` with a stable
`ProtocolErrorCode`, message, and optional data. The transport layer maps that
error to its wire response. Adapters translate implementation-specific errors
at their boundary.

Subscriptions atomically return a current snapshot, its event watermark, and a
live stream. Events after that watermark are strictly ordered: duplicates are
ignored and a gap fails with `stale_cursor`. The port retains no historical
events. A restored backend may begin a new event sequence, so its subscription
snapshot replaces every earlier watermark and projection. A standalone read
followed by a subscription is not gap-free; callers use the snapshot returned
by `subscribe` when they need live updates.

Runtime shutdown and transport detachment are distinct. `shutdown` releases a
bound runtime and its owned resources. Disconnecting a client only detaches its
transport and subscriptions.

The Unified backend's private store contains a versioned Core checkpoint,
Runtime recovery state, projection recovery state, a recovery journal, and a
quiescent committed-history export. The app server passes current
configuration at open time and maps typed storage or migration failures, but
does not inspect those private records.

Backend-specific capabilities do not belong in the common session interfaces.
They use narrow capability interfaces so an adapter can report them as
unsupported without requiring another adapter to emulate them.

## Rationale

Process and session lifetimes are different: one process locates and creates
many sessions, while each live mutation and event stream has one session owner.
The split prevents app-server handlers, storage readers, and execution runtimes
from becoming competing sources of session state.

## Agent Guidance

- Add session creation, lookup, listing, or forking to `SessionBackendHost`.
- Add common live-session reads or mutations to `SessionBackend` with typed
  parameters and results.
- Construct `AppServer` with an explicit Host factory. Do not add a legacy
  default inside `AppServer`.
- Put backend-specific behavior behind a narrow capability interface.
- Translate adapter errors to `SessionBackendError` at the port boundary.
- Keep snapshot creation and event subscription atomic.
- Keep backend selection invocation-scoped and catalogue operations
  backend-scoped.
- Acquire the shared session lease before reading either backend's private
  state.
- Import only a validated quiescent committed-history export and retain its
  provenance.

## Flag To User When

- A live-session read or mutation bypasses its backend.
- A handler reads backend-owned session storage directly.
- Two components can mutate or persist the same session state.
- Detach, shutdown, and interruption are treated as the same action.
- A backend-specific capability is being added to the common contract.
- A persisted routing record overrides the backend selected by the invocation.
- Public projection data is used as a restore or migration format.
