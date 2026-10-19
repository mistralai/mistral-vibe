# App-server protocol and lifecycle reference

This page records the client-facing protocol mechanics. Architecture decisions
live in [0009](../adr/0009-app-server-boundary.md), with Runtime ownership in
[0002](../adr/0002-core-engine-and-delivery-surfaces.md) and feature ownership in
[0018](../adr/0018-modular-app-server-capabilities.md). Those module boundaries
guide new work; the current dispatcher and adapters still contain coupling.

## Public contracts and transports

The authoritative method catalogue and parameter/result models are in
[`vibe.app_server.protocol`](../../vibe/app_server/protocol.py).
[`models`](../../vibe/app_server/models.py) owns public session, history,
callback, effect and resource values; focused modules own redacted views.
`ProtocolModel` provides strict camel-case serialization and unknown-field
rejection. Server-only adapters translate private values into these models.

The public Python facades are `AppServerHost`, `AppServerSession`,
`ClientToolHandler` and `SessionExitSummary`. Attached clients receive values,
not Runtime/Core objects or registries. Launch and setup code may perform
pre-session bootstrap without owning a live session.

Serialized in-process queues and newline-delimited stdio share JSON-RPC 2.0
models and handlers. In-process calls do not bypass serialization. Transport
queues are bounded; the stdio writer preserves order and applies backpressure.
Stdout carries JSON-RPC only; human logs use stderr or the configured log file.

The client's reader-to-`incoming()` handoff is intentionally unbounded. The
reader resolves request futures, while an event consumer can issue a snapshot
request during gap recovery. Bounding that handoff can block the reader behind
notifications while the consumer waits for a response, deadlocking both.
Transport backpressure must preserve this request/response progress guarantee.

## Initialization and attachment

Each connection follows this order:

1. Send `initialize` with client identity and advertised capabilities.
2. Receive server identity, protocol version, methods, callbacks and transports.
3. Send `initialized`.
4. Use passive host operations, or open a session through `session/start`,
   `session/resume` or `session/continue`.
5. Adopt the returned snapshot and consume the attached live event stream.

Ordinary requests require initialization, which occurs once per connection.
Server construction does not eagerly open a session. `AppServerHost` provides
passive catalogue, history, config-schema and workspace-trust operations.
`setup/status`, `setup/store-credential` and `setup/submit-choices` operate
without an attached session; the server owns credential persistence.
Opening a session transfers the connection to `AppServerSession`.

One `AppServer` instance currently owns one attached root and its child-session
registry. The protocol does not promise multiple simultaneous attached
observers of one Runtime.

Clients send typed action/resource requests. Servers send typed state/resource
notifications and participation requests (`callback/call` and advertised
`clientTool/*` operations). Queue editing and queued steering commit independently
of response delivery: their state updates may precede the response, and a lost
response does not undo acceptance. Other accepted responses precede notifications
caused by that action. Attachment establishes the snapshot, watermark and live
route atomically so there is no read/subscribe gap.

## Session operations and turns

`session/start` creates an empty attached session; resume loads saved state;
continue selects the latest eligible session. List, stored read and history
are passive operations. Fork creates an independent session from a supported
boundary; `session/stop` flushes and shuts down the attached session.

Turn input uses structured entries and content. App-server preparation and
Runtime execution determine model-visible input; clients do not construct
private messages or maintain a second prompt renderer.

A session has at most one active turn:

- `turn/start` requires idle state and starts work immediately.
- `turn/steer` adds input to the expected active turn, without queueing a future turn.
- `turn/interrupt` targets the expected active turn.
- `session/turn/enqueue` accepts context entries followed by at most one user
  entry as a separate future turn, without changing the active turn.

Expected turn IDs reject stale control requests. If enqueue finds an idle,
unpaused session, exactly one item is promoted after the enqueue response.
This handles busy-submit races without changing `turn/start` semantics.

## Accepted turn queue

Queue state belongs to the live session Runtime. It is currently process-local,
including queued items, pause state, edit holds, idempotency and completion
receipts: reconnecting to the same live session recovers public queue state,
but stop or process restart discards it. Durable execution recovery does not
imply persisted future prompts.

The `session/turn/queue/*` family provides read, remove, replace, edit/start,
edit/save, edit/cancel, edit/abandon, steer and resume. Queue rules are:

- Stable item IDs and FIFO order; adjacent prompts remain separate turns.
- Replace uses structured entries and preserves ID, creation time and position.
  A removed or already-started target returns `not_found`.
- Completed turns promote at most one next item. Failed or interrupted turns
  retain the remaining items and pause automatic draining.
- Remove and resume are idempotent. Enqueue and replace accept idempotency keys;
  keys remain reserved for the live session and conflicting reuse fails.
- A fixed size limit rejects overflow without modifying the queue.
- Shell/slash commands, scheduled loops and other unsolicited work are not
  prompt-queue items; unsolicited work cannot overtake accepted user turns.

Promotion removes the item before `turn/started`; the public turn retains its
queue item ID. Queued input enters public history only when accepted into
execution. Notification order is `turn/completed`, the queue update, then the
next `turn/started`.

Backends that support queued editing provide an exclusive edit identified by
the queue item ID and a client-generated `editId`. Start holds that item so it
cannot run; older queued items may continue. Save atomically replaces its
content and releases the hold. Cancel releases the hold with the original
content unchanged. Both allow draining unless the queue is paused. Edits have
no timeout or renewal request. Abandon clears the matching unfinished edit,
preserves the original item and pauses the queue. An accepted Save or Cancel
survives a lost response and takes precedence over later abandonment.

Queued steering takes session ID, queue item ID and expected active turn ID,
without resending content. Under the session lifecycle lock, Runtime submits
the prepared content and retires that exact queue item atomically. A successful
retry for the same expected turn returns its receipt without steering twice;
reuse with another turn conflicts. Stale turns, missing items and unsupported
queued context fail without queue mutation. The current implementation accepts
user-only queued steering and rejects items with context entries.

The adapter flushes projected events through the steering watermark. The Rust
CLI keeps queued entries pending until the matching `history/entryAdded` user
entry with `source = turn_steer` and the message entry ID arrives, preserving
output order. While steering is unresolved, later submissions, edits and removals
cannot overtake it. After disconnect, reconcile the snapshot: a matching
history entry means steering succeeded, an unchanged item means it did not,
and a turn carrying its item ID means normal promotion occurred. Do not
re-enqueue the message speculatively.

Busy-command policy is in [0012](../adr/0012-two-phase-slash-command-execution.md).
Clients do not schedule accepted turns or deferred settings independently.

## Public state and event reduction

`PublicSessionState` is a lossy renderable projection: format, session metadata,
event watermark, a history page, open callbacks, current/terminal turn, queued
turns and pause state, and the current model retry. It cannot restore execution.
Only Runtime reads its private execution store.

History entries are message, reasoning, effect, callback, checkpoint or notice
variants. IDs are stable. In-progress entries accept typed patches; completed
entries are immutable during normal event reduction. One effect spans call,
output, approval, result, duration and terminal state. Tool names are data;
bounded semantic categories select rich rendering with a generic fallback.

Compaction preserves session identity and appends a checkpoint. Rewind can
explicitly truncate in place or create a fork; clear/context replacement may
return a new session. Every such mutation returns authoritative replacement
state. Handoffs contain old identity, replacement state, watermark and log
summary; clients adopt them atomically before later events. Root replacement
is serialized and closes staged resources if adoption fails.

Projection events carry positive monotonic IDs within the loaded session.
The snapshot's `eventId` is its watermark. Reducers:

1. Ignore IDs at or below the watermark.
2. Accept the next ID.
3. Treat a larger ID as a gap.
4. Recover through `session/read` or a fresh atomic subscription, replacing
   the projection and reconciling public events.

Subscriptions do not promise historical replay across connections or processes.
After restore, adopt the new snapshot's watermark rather than assuming the
previous live event sequence is reusable as a durable log.

Notifications include session snapshots/handoffs/updates, history additions
and patches, queue updates, turn start/completion and session statistics.
Warnings, errors and resource notifications remain typed.

`PublicSessionState.retrying` is present only while the live turn waits for a
model retry. It clears before resumed output, terminal state, interruption,
shutdown or replacement. Numbered snapshots carry retry changes. Reducers keep
an already-loaded contiguous history prefix when a same-session live snapshot
overlaps it; explicit resync and handoff replace state. The unnumbered
`turn/retrying` compatibility notification remains for existing ACP/VS Code
clients. New reducers derive retry presentation from canonical state.

## Callbacks and client tools

The callback lifecycle records an open public entry and blocked effect, sends
`callback/call` to an advertised responder, receives delivery acknowledgement,
then accepts the semantic answer through **`callback/result`**. Runtime validates
correlation and resolves the pending execution request once. Identical semantic
retries are no-ops; conflicting second responses fail. Still-open callbacks
remain in `activeCallbacks` and are redelivered on live reconnect.

Client-hosted filesystem/terminal operations require advertised capability.
The server routes them through its tool-I/O contract, validates responses and
preserves Runtime permission enforcement and result ownership. Unsupported
operations fail explicitly.

## Product resources and child sessions

Resource APIs expose canonical public values rather than managers. When a
mutation affects several derived views, `runtime/updated` carries a canonical
`RuntimeSnapshot` for config, agents, tools, skills, hooks, MCP, connectors,
config issues and statistics. Refer to the protocol catalogue for callable
methods; a projected resource need not have a standalone list RPC.

Each child has its own session, Runtime and projection. The parent effect links
to its child ID instead of embedding private child state. Hosting code routes
child events, callbacks, I/O and cleanup. Derived sessions receive inherited
resolved configuration before tool discovery or prompt rendering.

The Rust CLI renders public values over the stdio API, in both interactive and
programmatic mode, and ACP translates protocol content and callbacks.
Clients own rendering and explicitly local facilities, while attached session
mutations use public APIs.

## Reconnect, shutdown and security

A replaced in-process connection initializes again, reattaches the live session
and adopts its snapshot, queue, open callbacks and current retry state. Stdio
EOF closes the server process. Restart restores Runtime durable state according
to supported recovery rules, not from client projections; externally visible
effects are not promised exactly-once execution across arbitrary failures.

Detachment releases connection subscriptions and callback claims. The server
first settles or cancels that connection's pending requests, then asks a
backend supporting queued editing to abandon its unfinished edit. Abandonment
clears the hold, keeps the original queued message and pauses the queue. The
client discards the draft and exits editing. Reconnecting leaves the queue
paused until an explicit Resume. An accepted Save or Cancel takes precedence
over disconnect cleanup, even when its response was lost.

Desktop Code keeps its Start subscription open while editing and abandons the
matching edit when that subscription closes. The native app-server Start
method remains unary and uses connection detachment for this cleanup.

`session/stop` is the durability and cleanup boundary: persist session-owned
state, stop pending work, close children and release owned processes, clients
and other execution resources. Cleanup must attempt all owned resources even
when one fails.

Tool/workspace/network/integration policy is enforced server-side. Rendering
metadata grants no permission. Public projections redact secrets and unsafe
internal errors; config views expose environment names rather than secret
values. Validate callback and client-tool results before execution-state
changes. Unknown protocol methods, fields and variants fail explicitly.

Boundary and lifecycle coverage lives in
[`tests/app_server`](../../tests/app_server),
[`client-e2e`](../../client-e2e),
[`tests/acp`](../../tests/acp) and source subprocess E2E tests.
