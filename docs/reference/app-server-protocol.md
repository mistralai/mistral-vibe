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
notifications and participation requests (`callback/call`, advertised
`clientTool/*` operations and advertised `session/spawn`). Queue editing and queued steering commit independently
of response delivery: their state updates may precede the response, and a lost
response does not undo acceptance. Other accepted responses precede notifications
caused by that action. Attachment establishes the snapshot, watermark and live
route atomically so there is no read/subscribe gap.

## Session operations and turns

`session/start` creates an attached session. It can also carry the session's
agent (`agentConfig.agent`), title, model, reasoning effort and first turn,
applied and pinned as the matching mid-session changes are: like
`config/model/write`, the model and level also become the user's default unless
`sessionOnly` is set. If a choice or the first turn fails, the server deletes
the session it opened and fails the start. Resume loads saved state;
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

A client that advertises `capabilities.sessionSpawn` in `initialize` hosts
sessions the model starts: its sessions are offered Vibe's `start_session`
tool, permission-gated like other Vibe tools (`ask` by default). The tool takes
`prompt`, an optional `title`, a `placement`, and an optional `model` and
`reasoning_effort` its description lists from the host's models; the server
checks both. A call sends **`session/spawn`** `{parentSessionId, prompt,
title?, checkout, idempotencyKey, model, reasoningEffort?, approvalPolicy}` to
that client. `checkout` is where
the session runs, resolved from the parent's live directory:
`{kind: "new_worktree"}`, the parent's Vibe-managed worktree
`{kind: "managed_worktree", path}`, or the parent's own directory
`{kind: "directory", path}`. `approvalPolicy` is always the parent's; `model`
and `reasoningEffort` are too unless the tool call names them. Another model
without a level gets that model's default level.
`idempotencyKey` is stable for one tool call, so a client can recognize a call
it already served. The client creates and runs the session its own way and
answers `{sessionId, title?, url?}`; a JSON-RPC error or a malformed answer is
a tool error the model reads. A disconnect, or 120 seconds without an answer,
reports the outcome as unknown, and an answer arriving later is logged and
dropped. Clients that do not advertise the capability never see the
tool.

A client that advertises `capabilities.sessionPeers` hosts the other sessions
of a session's project: its sessions are offered `list_sessions`,
`read_session`, `send_to_session` and `archive_sessions`. Listing and reading
are allowed by default and callable from programs, so a model filters sessions
on any field in code; sending and archiving ask, like `start_session`, and
only as direct calls. Each call sends the client a
request on the calling session's own connection, and the client answers for
that session's project only:

- **`session/peer/list`** `{sessionIds?, statuses?, limit, cursor?}`
  answers `{sessions, nextCursor?, notFound}`: the given sessions in that
  order, or the project's other sessions most recently active first, a page
  at a time, archived ones only when `statuses` names them. Each is
  `{sessionId, title?, status, statusDetail?, url?, updatedAt?, cwd?,
  startedBySessionId?, queuedMessages?, pullRequests?}`, a field left out
  when it says nothing: `pullRequests` is its artifacts with their state,
  checks, review and branches. `notFound` lists the asked ids that are not
  sessions of the project.
- **`session/peer/read`** `{targetSessionId, limit, cursor?,
  includeToolCalls, maxMessageChars}` answers at once with `{sessionId, title?, status,
  statusDetail?, url?, queuedMessages, messages: [{role, text}], nextCursor?}`:
  a page of its user and assistant messages, oldest first, the latest page
  unless `cursor` asks for the one before it, and with `includeToolCalls` a
  `tool` line per tool call naming what it ran or touched. A message longer
  than `maxMessageChars` is cut and says so. It never waits for the target.
- **`session/peer/send`** `{targetSessionId, text, idempotencyKey}` queues
  `text` as the target's next turn and answers `{sessionId, title?, url?,
  queued}`, `queued` when the message waits behind a running or queued turn,
  as the server's `waits` says.
- **`session/peer/archive`** `{sessionIds}` archives those sessions, as the
  user's archive action does, and answers `{archived: [{sessionId, title?}],
  failed: [{sessionId, error}]}`; one that is not a session of the project, is
  the caller itself, or is working, has queued messages or waits for the user,
  fails alone.

Session ids are the client's: the `clientSessionId` a `session/spawn` answer
carries, which `start_session` then returns to the model. `status` is
`working`, `idle`, `waiting_for_user` (an approval or question blocks it;
`statusDetail` says which), `failed` or `archived`; a session another process
holds (its lease, as `session/archive` with `requireIdle` sees it) is
`working`, and one no process holds is `idle`. The client refuses a send to the calling session
itself, and queues a sent message with `sentBySession: true` on
`session/turn/enqueue`. The turn such a message starts is not the user's: in
it, as in one another session started, the session cannot start or message
sessions until the user writes to it. A refusal or a
malformed answer is a tool error the model reads; a disconnect during a send
reports the outcome as unknown.

The server, which holds each session's turns and queue, decides what a client
could only race, and says why with `data.refusal` on a `conflict`.
`session/turn/enqueue` with `sentBySession` refuses `"archived"` once the
session is archived (read from its saved metadata, so an archive by another
process counts) and `"paused"` while its queue is paused, which would hold the
message until the user resumes it; otherwise it answers `waits: true` when the
message waits behind a running or queued turn. `session/archive` with
`requireIdle: true` refuses `"busy"` while the session is working: a turn runs,
is reserved or waits in the queue, or work a turn started (a background
process, a title) still runs. On a session this server has open, each check
and its change are one step, and a scheduled loop does not fire on an archived
session. For a session no process of this server has open, an archive holds
the session's lease while it saves, so no process opens it from the metadata
before it; a process that already holds the lease runs the session, which
`requireIdle` refuses as `"busy"` and a plain archive saves over as before (the
holder can then save its own metadata over the archive). A saved session no
process holds has an empty queue: a queue lives only in the process that runs
it.

`initialize` ignores client capabilities the server does not know, so a client
may declare one ahead of the servers that act on it. The rest of the handshake
stays strict. Servers released before this rule reject unknown capabilities.

`session/list` takes two optional params: `sessionIds` lists only those
sessions, reading no other, and `include: ["pullRequests"]` adds
`pullRequests: {[sessionId]: [artifact]}` for the page's sessions beside
`items`. An answer that did not ask for them is unchanged. A client sends them
only to an app server that asks it for `session/peer/*`, which is as recent.

`session/start` takes an optional `startedBySessionId`: the session that started
this one to hand it `firstTurn`. The session keeps it, `/clear` included, and
`session/list` items return it as `startedBySessionId`. Other responses leave it
out, so its absence there says nothing. App servers before it accept the param
but do not save it, so they list no starter.

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

### Experimental: subagent control

The `session/subagents/*` methods are experimental: they are listed in
`EXPERIMENTAL_SERVER_METHODS` and may change in a minor release. Each takes the
root's `sessionId` and acts on the subagents (child sessions) that root
spawned. A headless client uses them to finish a run: wait for the subagents a
turn left running, interrupt them on a stop, then read what each did.

- `session/subagents/wait` takes `timeoutMs` and returns `settled`, whether
  every subagent finished in time, and `running`, the subagents still running
  when it returned (`sessionId`, `name`, `agentType`).
- `session/subagents/interrupt` takes a non-empty `reason` and interrupts every
  running subagent. It returns one result per subagent it found running:
  `outcome` is `interrupted`, `notRunning` (it finished first) or `failed`,
  with an `error` when it failed. One subagent's failure does not stop the
  others.
- `session/subagents/read` takes an optional `timeoutMs` (default 5000) and
  returns `subagents`, one row per subagent in spawn order: `sessionId`,
  `parentSessionId`, `rootSessionId`, `name`, `agentType`, `spawnActionId`,
  `spawnCallId`, `status` (`running`, `idle`, `interrupted`, `failed`,
  `closed` or `unknown`), `sessionDir`, `model`, `usage` (input, output,
  cached input and total tokens), `costUsd`, `pricingModel`,
  `pricingFallback` and `steps`. `model` is the provider model the
  subagent's agent called, `null` unless the server saw it call exactly one.
  The server prices `usage` at that model's configured rates, cached input at
  the cached rate; `pricingModel` is the alias whose rates it used. When the
  subagent's model is unknown (no call seen, several models, or a model not
  in the config), it falls back to the root's rates and sets
  `pricingFallback`. Each store read must finish
  by the deadline; a read that does not leaves only its own fields `null`, and
  the subagent is still listed.

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
