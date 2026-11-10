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
the session it opened and fails the start. `project` (`{kind: "local" |
"cloud", id}`) stamps the session's metadata with its project once; forks,
rewinds and `/clear` replacements inherit their source's stamp. Without it the
session stays unstamped, as CLI sessions are. Resume loads saved state;
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
remain in `activeCallbacks` and are redelivered on live reconnect. When
`session/resume` or `session/continue` attaches a session whose turn is still
running, such as a turn recovered after a restart, the server first sends that
turn's `turn/started` again, so the client can follow and interrupt it.

Client-hosted filesystem/terminal operations require advertised capability.
The server routes them through its tool-I/O contract, validates responses and
preserves Runtime permission enforcement and result ownership. Unsupported
operations fail explicitly.

Tool implementations share the Harness `ProvidedToolExecutor` contract:

| Execution target | Executor | Implementation |
| --- | --- | --- |
| `client` | `ClientToolExecutor` | Connected client, over the existing JSON-RPC connection |
| `mcpServer` | `McpToolExecutor` | Independent MCP server, over stdio or HTTP |

Both reach the same provided-tool discovery, approval, hooks and history path.
A local MCP subprocess is still an `mcpServer`, not a `client` tool.

Desktop advertises `capabilities.clientTools: ["tools/call"]`,
`capabilities.agentPlugins: [{name, root}]` and
`capabilities.clientToolDefinitions: [{namespace, name, description, inputSchema, outputSchema}]`.
The client supplies the tool definitions and implementation. Its bundled Plugins
supply portable guidance and determine which tools belong to a pinned session;
they do not declare client tools as MCP servers or carry a `tools.json` proxy.

Client execution sends **`tools/call`** `{callId, name, input, toolCallId, caller}`.
The client returns `{output?, annotations?, error?}`; `error` has
`{message, code?, details?}`. Cancellation sends **`tools/cancel`** `{callId}`.
The server allows a call only when its namespace names a Plugin declared by
the client at initialization and that Plugin is enabled with built-in scope in
the caller's live, pinned session. The advertised `root` remains required by the
initialization contract and is checked for uniqueness; it is not compared with
the session's installed root, checkout or data root and grants no permission.
Disabling or releasing the Plugin revokes pending calls. Desktop dispatches on
the `caller` sent with the call.
Model arguments never select the caller or Code session.
No HTTP listener, bridge token, MCP marker or capability file is used.

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
`read_session`, `send_to_session` and `archive_sessions`, and also
`restore_sessions` when it advertises `capabilities.sessionPeerRestore` and
`mark_sessions_seen` when it advertises `capabilities.sessionPeerMarkSeen`. A
client that predates `session/peer/restore` or `session/peer/markSeen` fails
its whole connection on that request, so the server never sends it to a
client that did not advertise the capability.
Listing, reading and marking seen are allowed by default; sending, archiving
and restoring ask, like `start_session`. Only listing and reading are
callable from programs, so a model filters sessions on any field in code.
Each call sends the client a request on the calling session's own
connection, and the client answers for that session's project only:

- **`session/peer/list`** `{sessionIds?, statuses?, limit, cursor?}`
  answers `{sessions, nextCursor?, notFound}`: the given sessions in that
  order, or the project's other sessions most recently active first, a page
  at a time, archived ones only when `statuses` names them. Each is
  `{sessionId, title?, status, statusDetail?, url?, updatedAt?, cwd?,
  startedBySessionId?, queuedMessages?, isUnseen?, pullRequests?}`, a field
  left out when it says nothing: `isUnseen` when the user has not seen its
  latest activity, `pullRequests` its artifacts with their state, checks,
  review and branches. `notFound` lists the asked ids that are not
  sessions of the project.
- **`session/peer/read`** `{targetSessionId, limit, cursor?,
  includeToolCalls, maxMessageChars}` answers at once with `{sessionId, title?, status,
  statusDetail?, url?, queuedMessages, isUnseen, messages: [{role, text}], nextCursor?}`:
  a page of its user and assistant messages, oldest first, the latest page
  unless `cursor` asks for the one before it, and with `includeToolCalls` a
  `tool` line per tool call naming what it ran or touched. A message longer
  than `maxMessageChars` is cut and says so. It never waits for the target.
- **`session/peer/send`** `{targetSessionId, text, idempotencyKey, sender,
  delivery, hops}` hands `text` to the target through `session/turn/enqueue`,
  with `sender`, `delivery` and `hops` as they came, and answers `{sessionId,
  title?, url?, queued}`, `queued` as the server's `waits` says.
- **`session/peer/archive`** `{sessionIds}` archives those sessions, as the
  user's archive action does, and answers `{archived: [{sessionId, title?}],
  failed: [{sessionId, error}]}`; one that is not a session of the project, is
  the caller itself, or is working, has queued messages or waits for the user,
  fails alone.
- **`session/peer/restore`** `{sessionIds}` unarchives those sessions, as the
  user's restore action does, and answers `{restored: [{sessionId, title?}],
  failed: [{sessionId, error}]}`; one that is not a session of the project
  fails alone, and one that is not archived is left as it is.
- **`session/peer/markSeen`** `{sessionIds}` clears those sessions' unread
  mark, as the user opening them does, and answers `{marked: [{sessionId,
  title?}], failed: [{sessionId, error}]}`; one that is not a session of the
  project, is the caller itself, or is working or waits for the user, fails
  alone. A marked session turns unread again when it next finishes a turn.

Session ids are the client's: the `clientSessionId` a `session/spawn` answer
carries, which `start_session` then returns to the model. The model may name a
session by its id, a unique prefix of at least 8 characters of it, or its
title (case ignored); the server resolves each name against
`session/peer/list` before it asks the client, so a client only ever receives
ids. A name that matches nothing, or
more than one session, fails the tool call (one entry of `failed` for
`archive_sessions`, `restore_sessions` and `mark_sessions_seen`) with the
closest sessions' ids and titles, so the model can call again;
`list_sessions` lists it in `not_found`. `send_to_session` without `session_id`
goes to the session that started the caller. `status` is
`working`, `idle`, `waiting_for_user` (an approval or question blocks it;
`statusDetail` says which), `failed` or `archived`; a session another process
holds (its lease, as `session/archive` with `requireIdle` sees it) is
`working`, and one no process holds is `idle`. The client refuses a send to the calling session
itself, and queues the message's own text on `session/turn/enqueue` with the
request's `sender: {sessionId, title?}`: the calling session, named by the
server, its id mapped to the client's. A server that names no sender predates
`sender` here too and refuses it, so the client sends `sentBySession: true`
instead, read as a message from an unnamed session. A repeated delivery with the same `idempotencyKey` is
answered from the first one and never runs twice. The model reads the text as
it was sent, after a `<session_message from_session_id="…" from_title="…"/>`
block naming its sender; the server records
`{type: "vibe.session_message", sessionId, title?}` as the message's
`userDisplayContent`; public history, the queue and the session preview show
the text alone, so a client renders the message as one the session received
rather than one the user typed. Such a message never enters the queue the user
edits: while a turn runs, it joins that turn as a steer does, waking a turn that
waits for input; otherwise, with `delivery: "start_turn"` (the default) it
starts a turn, and with `delivery: "queue"` it enters the model's context and
public history without starting one, so the session's next turn reads it
(`waits: true`). The server counts `hops`, the messages and starts between
sessions in a row since a user wrote: each send and `session/spawn` carries one
more than the turn sending it, a turn the user starts, queues or steers resets
the count, and a message joining a turn raises it. Past `MAX_SESSION_HOPS` (32)
the server refuses `send_to_session` and `start_session`. It also refuses
`start_session` from a session another session started (read from the starter
the session saved, so it holds across restarts), and past
`MAX_TURN_STARTED_SESSIONS` (32) sessions started in one turn. Each refusal is a tool error whose `details.reason` is
`session_depth`, `started_sessions` or `session_hops`. The task a session hands with `start_session` tells the model
to report back that way when it is done, in a block public history leaves out,
so the starter learns the outcome without reading the session over and over. A refusal or a
malformed answer is a tool error the model reads; a disconnect during a send
reports the outcome as unknown.

The server, which holds each session's turns and queue, decides what a client
could only race, and says why with `data.refusal` on a `conflict`.
`session/turn/enqueue` from another session refuses `"archived"` once the
session is archived (read from its saved metadata, so an archive by another
process counts), `"paused"` while its queue is paused and no turn runs to join,
and `"busy"` when no turn can be joined or started after a few seconds of
retrying. `session/archive` with
`requireIdle: true` first waits up to 10 seconds for the session to settle (a
started session reports back from inside a turn that is about to end), then
refuses `"busy"` while the session is still working: a turn runs,
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

`session/archive` with `archived: true` and `includeStartedSessions: true`
also archives the sessions the session started, and those they started in
turn. Each is archived as with `requireIdle`, through whichever channel of this
process has it open, so its own save does not undo the archive. A started
session stays unarchived, with the sessions it started, when it is pinned
(`"pinned"`), stamped with another project than the starter's
(`"other_project"`), still working (`"busy"`), or its archive fails
(`"failed"`). The answer adds `startedSessions: {archived: [sessionId],
skipped: [{sessionId, reason}]}`; an answer that did not ask for it is
unchanged. Unarchiving ignores the flag.

A client that advertises `capabilities.agentWorktrees` lets its local
sessions move into git worktrees of their own: those sessions are offered
`enter_worktree`, and, when they start in the user's checkout, their system
prompt has the model ask the user (through `ask_user_question`) before its
first edit whether to work in a new worktree or in place. Other clients never
see the tool.

`initialize` ignores client capabilities the server does not know, so a client
may declare one ahead of the servers that act on it. The rest of the handshake
stays strict. Servers released before this rule reject unknown capabilities.

`session/list` takes two optional params: `sessionIds` lists only those
sessions, reading no other, and `include: ["pullRequests"]` adds
`pullRequests: {[sessionId]: [artifact]}` for the page's sessions beside
`items`. An answer that did not ask for them is unchanged. A client sends them
only to an app server that asks it for `session/peer/*`, which is as recent.

Every `PublicSession` carries its `project` stamp (`{kind, id}`), null while
unstamped. With `experimental_enable_local_projects` on, a listing gives an
unstamped session, or one stamped with a removed local project, the local
project whose folder is the session's working folder or contains it, and saves
that stamp. The deepest project folder wins. A session in a managed worktree
matches only a project on exactly its folder. When several projects share the
folder, it picks a single-folder project, then one whose primary folder it is,
then the oldest, then the lowest id. A session another process holds gets the match in
the answer only, and a later listing saves it; a legacy session is matched on
every listing.

`session/move` puts a root session in another project:
`{sessionId, project: {kind: "local", id} | {kind: "none"}}`. It needs
`experimental_enable_local_projects` and answers `{state}`. The session is
stamped with the project and moved to its primary folder, as `session/relocate`
moves it, without the same-repository rule. A session that leaves a managed
worktree releases it; the worktree stays on disk. With `kind: "none"` the
session keeps its folder. A working session, or one another connection or
process holds, is refused as a `conflict` before anything moves; an unknown
project is `not_found`. A session only the
legacy store has is refused as `invalid_params` until it is opened.

`session/start` takes an optional `startedBySessionId`: the session that started
this one to hand it `firstTurn`, and `hops`, the `session/spawn` request's. The session keeps it, `/clear` included, and
every `PublicSession` of it (listed, read, or updated) returns it as
`startedBySessionId`; a session nobody started leaves it out. App servers before
it accept the param but do not save it, so they report no starter.

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
