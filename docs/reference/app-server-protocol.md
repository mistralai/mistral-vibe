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

1. Send `initialize` with client identity and advertised capabilities, and
   optionally `account` (`{organizationId, workspaceId, userId}` or `null`):
   opaque strings naming the account the client is signed in to.
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
agent (`agentConfig.agent`), approval policy (`agentConfig.approvalPolicy`),
title, model, reasoning effort and first turn,
applied and pinned as the matching mid-session changes are: like
`config/model/write`, the model and level also become the user's default unless
`sessionOnly` is set. If a choice or the first turn fails, the server deletes
the session it opened and fails the start. `project` (`{kind: "local" |
"cloud", id}`) stamps the session's metadata with its project once; forks,
rewinds and `/clear` replacements inherit their source's stamp. Without it the
session stays unstamped, as CLI sessions are. Resume loads saved state;
continue selects the latest eligible session. List, stored read and history
are passive operations. Fork creates an independent session from a supported
boundary; `session/stop` flushes and shuts down the attached session, leaving
a running turn to resume (see [Reconnect, shutdown and
security](#reconnect-shutdown-and-security)).

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

`turn/start` takes an optional `injected` flag (default `false`); `turn/steer`
does not, and rejects it as an unknown field. Injected input reaches the model like any other input but creates no
public user entry: no `history/entryAdded` user message, no bubble to render.
The server uses it for its own notes and goal continuations; a client sending
user input leaves it unset.

A turn that fails carries a `PublicError` whose `code` names the cause.
`usage_limit` means the provider refused the request because the account has
no credit left (HTTP 402); retrying does not help until credit is added.

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

Projection events carry positive monotonic IDs within their session: a
connection carries the root's sequence and, for a client that asks for them,
one sequence per child (see below). The snapshot's `eventId` is its watermark.
Reducers, per session:

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
turn's `turn/started` again, so the client can follow and interrupt it. Its open
callbacks follow as `callback/call`, after the resume response and after that
`turn/started`; if the event stream does not deliver `turn/started` within five
seconds, they are sent anyway.

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
`prompt`, an optional `title`, a `placement`, a `link`, and an optional `model`
and `reasoning_effort` its description lists from the host's models; the server
checks both. A call sends **`session/spawn`** `{parentSessionId, prompt,
title?, checkout, idempotencyKey, model, reasoningEffort?, approvalPolicy,
agent?, attached?}` to that client. `attached` is `false` when the model chose `link:
"detached"`: the client starts an ordinary top-level session and leaves its
`startedBySessionId` unset, so it is not archived with the caller and does not
report back. It is left out when true, and a missing value means attached: the
client starts the session with `startedBySessionId` set to `parentSessionId`.
`parentSessionId` names the caller either way. `checkout` is where
the session runs, resolved from the parent's live directory:
`{kind: "new_worktree"}`, the parent's Vibe-managed worktree
`{kind: "managed_worktree", path}`, or the parent's own directory
`{kind: "directory", path}`. `approvalPolicy` is always the parent's, and so
is `agent`, left out on the default agent; `model`
and `reasoningEffort` are too unless the tool call names them. Another model
without a level gets that model's default level.

The client passes `parentSessionId` back as `spawnedBySessionId` on the
`session/start`, attached or detached; it is separate from
`startedBySessionId`, which only links a child that reports back. The server
then runs the session on the policy its starter runs when that `session/start`
is processed, read live when this process holds the starter open and from its
saved pin otherwise, so a policy the user tightened meanwhile holds; the client
never chooses it. The start is refused (`invalid_params`, with `data.reason`)
when it asks for another policy, through `agentConfig.approvalPolicy` or a
policy name in `agentConfig.agent` (`policy_must_match_starter`), when it sets
`agentConfig.autoApprove` (`auto_approve_on_spawned_start`), or when the server
cannot read the starter (`starter_unknown`). A start that names only
`startedBySessionId`, as older clients send it, is held to the same rules when
the server can read the starter.
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
  delivery, hops, reply?}` hands `text` to the target through
  `session/turn/enqueue`, with `sender`, `delivery`, `hops` and `reply` as they
  came, and answers `{sessionId, title?, url?, queued}`, `queued` as the
  server's `waits` says.
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

A client that advertises `capabilities.sessionPeers` takes replies: when a
turn another session started ends (a handed task, or a `start_turn`
message from that session), the server sends that session one
`session/peer/send` with `reply: true`, `delivery: "start_turn"`, one hop more
than the turn, and the turn's final assistant message, cut to the message
limit. A turn that did not simply finish puts its outcome first (failed with
its error, stopped by the user, or cut at a limit), as does one that ended
without a reply. It goes out once the session no
longer runs that turn's work, so an archive right after it is not refused as
busy. A turn the user, a `queue` message or a reply started sends none, so two
sessions never keep replying to each other. The reply owed is saved with the
session before the turn runs and cleared once the client takes it or the
target is archived or gone; `session/list` with `owesReply: true` lists the
sessions still owing one, each with `owesReply`, and the client resumes them
when it starts so a reply a restart interrupted still goes out, once. It leaves
out the sessions stamped with another owner (see below); a session with no
stamp is listed.

`session/list` with `interrupted: true` lists the root sessions an app server
that stopped or died left with a turn still running, in the session or in one
of its subagents, and no process holding the session. A subagent left mid-turn
lists its root, since resuming the root carries the subagent on; subagents are
never listed themselves, and neither are legacy sessions. A client resumes
them when it starts. A turn whose recovery was started twice without the turn
settling (the process died again each time) is no longer listed, so a turn
that crashes the app server is not resumed on every launch; `session/resume`
by id still continues it. An older app server rejects `interrupted` as invalid
params; the client then resumes nothing and goes on.

A connection that sent an `account` in `initialize` has an owner: its
`clientInfo.entrypoint` and that account. One without an account (`null` or
absent) has none: it stamps nothing and lists nothing under
`interrupted: true`. The server stamps a root session with the owner of the
connection that drives it, replacing any earlier stamp: on `session/start`, a
fork, a rewind fork or a history clear (the new session), and on every
`turn/start` and `turn/enqueue`. Opening an existing session
(`session/resume`, `session/continue`) does not stamp it. A session never
stored yet is stamped when its first turn stores it. `session/list` with
`interrupted: true` lists only sessions whose stamp equals the listing
connection's owner exactly, entrypoint and account both. A session with no
stamp, written by an older server or a connection without an account, is never
listed. So a client never resumes the turns of another client or another
account; they stay on disk until their owner lists them.

`session/resume` or `session/continue` of a session stamped with another owner,
whose tree a stopped process left with a turn running, opens it without
carrying that turn on: nothing is sent or run, the turn stays as it was, and
`turn/start`, `turn/steer` and `turn/enqueue` on it fail with `conflict`
(`harnessCode: turn_conflict`). `turn/interrupt` ends it as interrupted, still
without running anything, after which the connection may start turns there.
While that connection holds the session in the same app server, the session
stays listed under `interrupted: true` for its owner, whose own resume carries
the turn on. Held by another app server, it is not listed for its owner until
that server lets it go. A session with no stamp, or a connection with no
owner, carries the turn on as before.

`session/resume` with `interruptLeftTurn: true` ends a turn a stopped process
left running in the session, and the turns it left its subagents running, as
interrupted instead of carrying them on, whoever opens it: no model call is
sent again, no tool runs, and no question or approval is asked again (the ones
left open are closed). A client uses it to stop a session it listed under
`interrupted: true` without running it. An older server rejects the field as
invalid params.

An older server rejects `account` as an unknown field; the client then
initializes again without it.

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
the server refuses `send_to_session` and `start_session`. A detached session
keeps the hops of its first turn too, so detached starts cannot chain past it.
It also refuses `start_session` from a session attached to the one that started
it (read from the starter the session saved, so it holds across restarts), and
past `MAX_TURN_STARTED_SESSIONS` (32) sessions started in one turn. Each refusal is a tool error whose `details.reason` is
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
prompt carries a "## Worktrees" section telling the agent where to edit. The
`auto_worktree` setting picks it: off (the default) the agent edits the
checkout in place and calls `enter_worktree` only when the user asks to work
in a worktree; on it calls `enter_worktree` before the first edit, unless the
user asks to work in their checkout. Either way, a request about work already
in progress in the checkout (fixing, finishing or testing it) is edited in
place, no mode has the agent ask, and an explicit user request always wins. A
session started in a managed worktree gets the tool but no section: the
choice was already made. Other clients never see the tool.

The worktree settings persist in the user config through the host methods
`workspace/git/worktrees/limit/update` (retention: `{limit: int}` answered
with `{limit, failures}`) and `workspace/git/worktrees/auto/update`
(placement: `{auto: bool}` answered with `{auto, failures}`), which the
config view reports as `worktreeLimit` and `autoWorktree`. `failures` lists
the config layers that could not be saved; an empty list means the setting
persisted.

`initialize` ignores client capabilities the server does not know, so a client
may declare one ahead of the servers that act on it. The rest of the handshake
stays strict. Servers released before this rule reject unknown capabilities.

`session/list` takes two optional params: `sessionIds` lists only those
sessions, reading no other, and `include: ["pullRequests"]` adds
`pullRequests: {[sessionId]: [artifact]}` for the page's sessions beside
`items`. An answer that did not ask for them is unchanged. A client sends them
only to an app server that asks it for `session/peer/*`, which is as recent.

Every `PublicSession` carries its `project` stamp (`{kind, id}`), null while
unstamped. A listing gives an
unstamped session, or one stamped with a removed local project, the local
project whose folder is the session's working folder or contains it, and saves
that stamp. The deepest project folder wins. A session in a managed worktree
matches only a project on exactly its folder. When several projects share the
folder, it picks a single-folder project, then one whose primary folder it is,
then the oldest, then the lowest id. A session another process holds gets the match in
the answer only, and a later listing saves it; a legacy session is matched on
every listing.

`projects/remove` deletes a local project and the folder links that would
import it back; its folders, sessions and worktrees stay. Its sessions then
join the project on their folder, or no project. A listing purges the projects
an older app server only hid.

`session/move` puts a root session in another project:
`{sessionId, project: {kind: "local", id} | {kind: "none"}}`. It answers
`{state}`. The session is
stamped with the project and moved to its primary folder, as `session/relocate`
moves it, without the same-repository rule. A session that leaves a managed
worktree releases it; the worktree stays on disk. With `kind: "none"` the
session keeps its folder. A working session, or one another connection or
process holds, is refused as a `conflict` before anything moves; an unknown
project is `not_found`. A session only the
legacy store has is refused as `invalid_params` until it is opened.
An open session that leaves a folder no local project claims keeps it as a
workspace root, unless that folder is the home folder or contains the
target. A move of an open session patches `/project` on its
`session/updated` stream.

A client that advertises `capabilities.agentProjects` follows that patch,
so its local root sessions are offered `list_projects` and
`move_to_project`. `list_projects` answers each local project's `id`,
`name`, primary `folder`, whether that folder is `trusted` in
`trusted_folders.toml`, and whether the session is `current`ly in it.
`move_to_project {project_id}` only records the move: it lands once the turn
completes, as `session/move` would, so the project's AGENTS.md, config, MCP
servers and hooks apply from the next turn. A turn that is interrupted or
fails drops the move, and so does a call naming the current project or no
project. A turn started while the move lands waits for it. A move a queued
turn beats is retried when that turn ends; a move that fails, as when a
background process runs, posts a notice. The call asks for approval unless the
target folder is trusted or it cancels; an approval releases that move only. A `permission` set in
`[tools.move_to_project]` overrides the trust check.

`session/start` takes an optional `startedBySessionId`: the session that started
this one to hand it `firstTurn`, and `hops`, the `session/spawn` request's, which
`firstTurn` carries with or without a starter. The session keeps the starter, `/clear` included, and
every `PublicSession` of it (listed, read, or updated) returns it as
`startedBySessionId`; a session nobody started leaves it out. App servers before
it accept the param but do not save it, so they report no starter.

## Session goal

A root session holds at most one durable goal: an objective Vibe keeps working
on between turns until it ends. The server owns it entirely: storage, the
continuation turns, accounting and every stop. Clients render
`PublicSession.goal` and send `goal/*` requests; they schedule nothing.

### `PublicSession.goal`

`PublicSession.goal` is absent while the session has no goal. `session/read`
and `session/snapshot` carry it, and `session/updated` patches it live with
JSON Patch operations, as for any other `PublicSession` field:
`{op: "add", path: "/goal", value: PublicGoal}` sets it, `{op: "remove",
path: "/goal"}` clears it, and a change to the current goal usually arrives as
`add`, `replace` or `remove` operations on its fields, such as
`{op: "replace", path: "/goal/status"}`,
`/goal/tokensUsed` or `/goal/timeUsedSeconds` while a goal turn runs, or
`/goal/goalId` and the other fields when a goal replaces it. Apply every
operation whose path starts with `/goal`, not only `add` and `remove`.

| Field | Type | Meaning |
| --- | --- | --- |
| `goalId` | string | Identity; a replaced goal gets a new one. |
| `revision` | int | Bumped on every change, for compare-and-swap. |
| `objectiveRevision` | int | Bumped only when the objective text changes. |
| `objective` | string | At most 4,000 characters. |
| `status` | `active`, `paused`, `blocked`, `complete`, `budget_limited`, `usage_limited` | See below. |
| `statusReason` | `null`, `user`, `interrupted`, `suspended`, `model`, `no_progress`, `tool_failures`, `turn_failed` | Why the goal left `active`; `null` while active and for budget and usage limits. |
| `tokenBudget` | int or `null` | `null` means no budget; under `max_goal_token_budget` a goal always has one. |
| `tokensUsed` | int | Billable tokens of the turns bound to the goal, subagents included; live while a goal turn runs. |
| `timeUsedSeconds` | int | Time those turns ran, without the time spent waiting for the user; live while a goal turn runs. |
| `createdAt`, `updatedAt` | int | Milliseconds since the epoch. |

Status transitions:

- `paused`: the user paused it (`user`), interrupted a goal turn
  (`interrupted`), or reopened the session while it was active (`suspended`);
  or the model paused it at the user's request (`model`).
- `blocked`: the model reported a blocker (`model`), three continuations in a
  row called no tool (`no_progress`), three continuations in a row had only
  failed tool calls (`tool_failures`), or a goal turn failed (`turn_failed`).
- `budget_limited`: `tokensUsed` reached `tokenBudget`. The limit is soft:
  the running turn is not interrupted, and no new continuation starts.
- `usage_limited`: a goal turn failed with the `usage_limit` turn error.
- `complete`: the model marked the goal complete. Once the turn that
  completed it ends, the server records the `completed` notice and clears the
  goal: `PublicSession.goal` is removed and `goal.json` deleted.

### `goal/*` methods

- **`goal/get`** `{sessionId}` returns `{goal: PublicGoal | null}`. It and the
  `goal/set` response count the goal's running turns, as `PublicSession.goal`
  does.
- **`goal/set`** `{sessionId, idempotencyKey?, expectedGoalId?,
  expectedRevision?, replace?, objective?, status?, tokenBudget?}` returns
  `{goal}`. Without a current goal, or with `replace: true`, it creates a new
  goal and needs `objective`; `status: "paused"` creates it paused. Otherwise
  it edits the current goal: a new `objective`, a `status` of `active`
  (resume) or `paused`, and `tokenBudget`. An omitted `tokenBudget` keeps
  the budget; an explicit `null` removes it, or sets it to
  `max_goal_token_budget` when that is set. Raising or removing the budget of
  a `budget_limited` goal makes it active again; resuming it without that
  fails. A request that changes nothing returns the goal unchanged; pausing a
  goal that is already `paused`, whatever its `statusReason`, changes nothing.
- **`goal/clear`** `{sessionId, idempotencyKey?, expectedGoalId}` returns
  `{cleared}`, `false` when there was no goal.

`expectedGoalId` and `expectedRevision` guard against concurrent changes: the
model, the server and other clients also move the goal. Every new goal starts
at revision 1, so an edit or a clear of a goal the client read sends its
`expectedGoalId` too: a goal replaced meanwhile then fails with `conflict`
instead of taking the edit. An edit (`replace` false) that names either one
while the session has no goal also fails with `conflict` rather than creating
a goal; `replace: true` creates one whatever it expected. Every mutation is
persisted before it returns, and a retried request with the same
`idempotencyKey` returns the recorded answer without changing the goal twice.
The requests work while a turn runs; the model learns of the change through a
hidden note before its next turn, so a running turn finishes on what it had.

| Error | When | Client action |
| --- | --- | --- |
| `conflict` | `expectedGoalId` or `expectedRevision` is stale, or names a goal that no longer exists. | Reload the goal, then retry or tell the user. |
| `invalid_params` | Empty or over-4,000-character objective; budget `<= 0` or above `max_goal_token_budget`; a new goal without an objective; resuming a `budget_limited` goal; editing a `complete` goal. | Show the message. |
| `not_implemented` | The session cannot hold a goal (headless, or closed). | Hide or disable goal controls. |
| `method_not_found` | The app server predates goals. | Treat goals as unavailable. |

### Continuations and life cycle

While the goal is `active` and the session is idle, the server starts the next
continuation turn: after every queued user turn, and after any due scheduled
loop. A paused queue, an open callback or an archived session holds it back,
and so does a session no client is subscribed to: the server charges goal
turns from its event stream, so it starts none it could not charge. When a
client subscribes again, the server settles the goal turns that ended in
between.
A continuation is an injected turn (see [Session operations and
turns](#session-operations-and-turns)): it creates no user history entry, and
its `turn/started` and `turn/completed` are otherwise ordinary.

The model sees three tools on the root session only, never in subagents:
`get_goal`, `create_goal` (only when the user asks for a goal; it fails while
an unfinished goal exists) and `update_goal` (`complete`, `blocked`, or
`paused` at the user's request; only an `active` goal moves). They never ask
for approval. Their results count the goal's running turns, as
`PublicSession.goal` does. `update_goal` with `complete` fails with a
conflict when the calling turn started before the user's last objective edit:
the error quotes the new objective, so the model checks it before completing.

Interrupting a goal turn (`turn/interrupt`, or an approval answered with
"cancel turn") pauses the goal on the server; the client sends nothing else.
A disconnect or restart does not count as a user interrupt.

The goal is stored in `goal.json` beside the session. When the session is
reopened, an active goal comes back `paused` with `statusReason: "suspended"`,
so no work starts until the user resumes it. `session/history/clear` and
`session/fork` start without a goal; `session/rewind` hands the goal and its
counters to the rewound session. A rewind into a new session keeps the goal
notices in the source session: the fork's copied entries carry no turn ids and
share the fork's time, so the notices could not be placed among them. Headless sessions (`vibe -p`, `vibe-acp`)
have no goal.

### Goal notices in the transcript

The server adds a `notice` history entry for each step a reader of the
transcript needs to follow the goal. The app server keeps these notices itself,
in `goal-notices.json` beside the session, and places them in the history it
returns: `history/entryAdded` delivers one live, and `session/read` and
`session/history/list` return them after a resume. A page that does not start
at the first entry shows a notice only when it shows the notice's turn (or,
for a notice without a turn, its time), and never as its first item, so the
page's cursor stays a history entry and paging repeats and skips nothing. An
in-place rewind drops the notices of the turns it removes, and the notices
without a turn recorded after the rewound message. Its `detail` has `kind: "goal"`:

| Field | Type | Meaning |
| --- | --- | --- |
| `event` | `set`, `edited`, `paused`, `resumed`, `cleared`, `continued`, `completed`, `blocked`, `budget_limited`, `usage_limited`, `interrupted` | What happened. |
| `goalId` | string | The goal it is about. |
| `objective` | string or `null` | Set on `set`, on `edited` when the objective changed, and on `completed`, since a complete goal then leaves the session. |
| `status`, `statusReason` | as in `PublicGoal` | The goal's state right after the event. |
| `continuation` | int or `null` | On `continued`: the continuation's number for this goal. |
| `tokensUsed`, `tokenBudget`, `timeUsedSeconds` | as in `PublicGoal` | Counters right after the event. |
| `error` | string or `null` | On `blocked` with `statusReason: "turn_failed"`: the failed turn's error message, at most 300 characters. |

The user's own actions (`goal/set`, `goal/clear`) record `set`, `edited`,
`paused`, `resumed` or `cleared`, and a goal the model creates records `set`.
Each continuation records `continued` with its `turnId`, once that turn has
started and only if it works on the goal: a continuation that starts just as
the goal stops records nothing and is interrupted. The server starts no
continuation until the end of every goal turn it saw start has been
processed, so a failed turn blocks the goal before the next one could start. A goal that stops
records `completed`, `blocked`, `budget_limited`, `usage_limited`,
`interrupted` for an interrupted goal turn, or `paused` (with
`statusReason: "model"`) when the model paused it, whoever stopped it. A stop made
during a turn is recorded when that turn ends, with its `turnId` and the
counters that include it, so a client can show it after that turn's output even
when a queued turn has already started. A goal blocked by a failed turn says
why in `message` and `detail.error`, and what to do next: `/goal resume` when
the error may pass on a retry, or `/rewind` or a new session when the same
request would fail again (a 4xx other than a timeout, conflict or rate limit,
a context or image error, an invalid model or key). Other changes the model makes stay
visible as its goal tool calls. `message` is a plain-text fallback, such as `Goal set:
<objective>` or `Working on the goal`, for clients without a dedicated
rendering. The model never sees these notices.

`max_goal_token_budget` caps the budget a goal may set, and is the budget of
a goal set without one (`/goal <objective>`, `tokenBudget: null`, or the
model's `create_goal`); unset allows any budget and no budget. The server
records `vibe.goal.created`, `vibe.goal.updated` and `vibe.goal.deleted`
telemetry with ids, statuses and counts, never the objective text.
`vibe.goal.updated` fires on a status change, and on an edit that keeps the
status, where `has_objective_changed` and `has_token_budget_changed` say what
the user changed and `objective_chars` gives the new objective's length.

### Agent and approval policy

Every session runs one agent and one approval policy, chosen independently.
The agent (`default`, `lean`, a custom or plugin agent) sets instructions,
model and tools; the policy (`ask`, `accept-edits`, `smart-approve`,
`auto-approve`, or a file overriding one) sets which tool calls run without
asking. The server decides which of each a session may run and lists them; a
client renders those lists and never filters them by its own rules.

- `session/agent/update` `{sessionId, agentName}` switches the agent in place
  and keeps the policy: same session, same history, with an `agent_change`
  checkpoint in the transcript. The new agent's model and tools apply from the
  next turn, and so do its instructions: that turn's system prompt is rebuilt
  for the running agent, which costs one uncached request. `session/approvalPolicy/update` `{sessionId, approvalPolicy}`
  switches the policy and keeps the agent, mid-turn included: the running
  turn's next tool call is gated by the new policy. Both answer a
  `RuntimeMutationResponse`.
- `RuntimeSnapshot` and `agents/list` carry `activeAgent`/`agents` (agent
  summaries `{name, displayName, description, agentType}`, subagents included
  with `agentType: "subagent"`) and `activeApprovalPolicy`/`approvalPolicies`
  (`{name, displayName, description, safety}`, in cycle order). A session in
  `session/list` and `session/read` carries `agent` and `approvalPolicy`.
- A refused switch or start is `INVALID_PARAMS` with `data.reason`:
  `not_found`, `disabled_by_organization`, `auto_approve_disabled`,
  `install_required`, `subagent` or `unavailable`.
- Compatibility with clients from before the split: a policy name sent as
  `agentConfig.agent` or `agentName` selects that policy, and
  `agentConfig.autoApprove` starts the session in `auto-approve`. Without a
  requested policy, a session starts in `default_approval_policy`, whatever its
  agent.
- `session/spawn` carries the caller's `approvalPolicy`, its `agent` unless it
  runs the default agent; the client starts the child with that agent and
  `spawnedBySessionId`, and the server gives it the caller's policy.

```json
// before
{"method": "session/agent/update", "params": {"sessionId": "s1", "agentName": "auto-approve"}}
// after
{"method": "session/approvalPolicy/update", "params": {"sessionId": "s1", "approvalPolicy": "auto-approve"}}
{"method": "session/agent/update", "params": {"sessionId": "s1", "agentName": "task-lead"}}
```

An agent file's settings that an agent may not set, and a project's override
of an approval policy, are dropped and listed in the snapshot's `issues` with a
machine-readable `code` (left out for issues without one):

```json
{"file": "/repo/.vibe/agents/loose.toml", "code": "agent.setting_ignored",
 "message": "Agent 'loose' sets 'tools.bash.allowlist', which an agent may not set; it is ignored. ..."}
{"file": "/repo/.vibe/agents/ask.toml", "code": "approval_policy.project_override_ignored",
 "message": "A project cannot redefine the approval policy 'ask'; this file is ignored. ..."}
```

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

The root's stream summarizes each child in `session/childSessionUpdated`. A
client that advertises `capabilities.childSessionEvents` also receives each
child's own projection events on the root's connection, tagged with the child's
`sessionId` and numbered on the child's own sequence, so it can keep a replica
of the child as it does of the root. A child's callback then also arrives as a
history entry of the child. `session/read` of a live child answers the state
its next event continues. Clients that do not advertise the capability get
only the summaries.

The Rust CLI renders public values over the stdio API, in both interactive and
programmatic mode, and ACP translates protocol content and callbacks.
Clients own rendering and explicitly local facilities, while attached session
mutations use public APIs.

### Storage usage

`storage/usage/read`, `storage/usage/refresh` and the `storage/usage/updated`
notification report how much disk Vibe uses, in total and per session. They
need no session: a connection with none attached (Desktop's catalogue
connection) can call them. A client finds support by calling
`storage/usage/read`; an older server answers "method not found".

- `storage/usage/read` takes `{}` and answers at once from the shared cache:
  `status` (`up_to_date`, `refreshing`, `partial` or `failed`), `snapshot`
  (`null` before the first scan), `disk` (total, free, `lowThresholdBytes` =
  the lower of 10% of the disk and 20 GB, `isLow`, read live) and `stale`
  (an archive, delete or worktree removal happened after the scan started).
  Each session row's `open` and the snapshot's `archiveSuggestion` and
  `deleteSuggestion` are computed at read time. `archiveSuggestion` lists
  active sessions idle 30+ days and the worktrees archiving them frees;
  `deleteSuggestion` lists archived sessions idle as long, and its
  `reclaimableBytes` adds their histories to the worktrees no session would
  still use. A worktree not measured yet counts for nothing in
  `reclaimableBytes`; when one the action would free is unmeasured, both
  suggestions set `reclaimableAtLeast` (absent, so false, from a server
  before it) and the number is a lower bound. A new managed worktree no
  session has joined yet is not offered for cleanup for its first 10
  minutes. A session row's `historyPath` is the absolute path
  of its own history folder (its subagent children's folders count in
  `historyBytes` but sit beside it), for a local client to reveal; it is
  `null` from a server before it, and never sent in telemetry. A row's
  `worktreeIds` lists every worktree the session claims, `worktreeId` (the
  one its folder is in) first; it is empty from a server before it.
  `leftoverCleanup` lists what `storage/leftovers/clean` would remove:
  `worktreeIds` (managed worktrees no session claims any more) and
  `unknownFolders` (`id`, `path`, `bytes`: folders of a worktree bucket with
  no claim and no `.git`), with `reclaimableBytes` their total.
- `storage/sessions/delete` takes `sessionIds` and is a plain delete of
  those sessions, archived or not, exactly as `session/delete` deletes one:
  each goes through it, one at a time, unless a live process holds its
  lease, checked again right before that session is deleted. Then each
  worktree a deleted session claimed is removed when no other session
  claims it any more (the rule `deleteBytes` and `deleteSuggestion` use,
  applied to the claims on disk at removal time), through the same worktree
  release as `storage/leftovers/clean`, so every client removes the same
  worktrees. It answers `removed` and `kept` (`id`, `reason`: `in_use`,
  `not_found` or `error`), `removedWorktreeIds`, `keptWorktrees` (a worktree
  that failed is kept with `error`; the others still go) and `freedBytes`
  (the removed histories and worktrees, as the last measurement sized them;
  `freedBytesAtLeast` is true when a removed worktree was not measured yet,
  so `freedBytes` is a lower bound), then refreshes. A session row's `deleteBytes`, set at every read from the
  snapshot, is what deleting it alone frees: its history and each worktree
  no other session claims; `deleteBytesAtLeast` is true when one of those
  worktrees is not measured yet, so `deleteBytes` is a lower bound.
  `deleteBytes` is `null` (unknown) from a server before it.
- `storage/leftovers/clean` takes `worktreeIds` and `folderIds` and answers
  `removed` (ids) and `kept` (`id`, `reason`: `claimed`, `in_use`,
  `kept_dirty`, `not_found`, `unsafe`, `error` or `too_large_to_check`); one
  item failing is kept with `error` and never stops the others, and an id no
  path can hold (a NUL byte, say) is kept as `unsafe`. When the call fails
  as a whole, what it removed before is still answered in `removed`. Every check runs again at
  removal time. A worktree is kept while a session folder its claim names
  is on disk, or while its claim names no session and is younger than 10
  minutes; then it goes through the plain worktree removal: kept while a
  live process holds it or it is being created. When it has uncommitted
  changes, untracked files or commits since it was created, they are saved
  before its folder goes as one commit, on top of its HEAD, at
  `refs/vibe/reaped/<name>`, so committed but unpushed work stays reachable
  from that ref even though the branch Vibe created is deleted; ignored
  files, and commits no longer reachable from HEAD, are not saved, and a
  later removal of a worktree with the same name replaces the ref. Nothing
  else records the removal. A folder must be a real folder directly in a bucket,
  strictly inside the worktrees root (no symlink), with no claim, no
  `.git`, no open session working in it, and no folder in it, at any depth,
  changed in the last 10 minutes (only folder times are read: a build
  creating files shows, a file rewritten in place does not). That check
  gives up after 5 seconds or a million entries; a folder it could not
  finish is kept as `too_large_to_check`, and this app server process then
  leaves it out of `leftoverCleanup.unknownFolders` (and its
  `reclaimableBytes`) until the folder's own modification time changes or
  the process restarts. It still counts in Other Vibe data
  (`worktrees_unknown`), for the user to remove by hand. A removal starts a
  refresh.
- `storage/usage/refresh` takes `maxAgeMs`, `full` and `waitForSessionIds`, all
  optional, and answers `started`, or `started: false` with `reason`
  `fresh`, `already_running` or `running_elsewhere`. A plain refresh while
  another process scans reports `running_elsewhere` and forwards that scan's
  result. `full` and `waitForSessionIds` wait for the other scan, at most 11
  minutes, then run their own; past that they report `failed`.
  `waitForSessionIds` waits up to 60 s for the worktree folders of those
  sessions to go; the result then says `complete: false` if one stayed.
  The answer also carries `scanId`, the scan whose result will answer the
  request: the new scan when one started, the scan under way for a plain
  refresh during one, the scan queued behind it for `full` or
  `waitForSessionIds`, and this process's forwarding of the other process's
  scan for `running_elsewhere`. It is absent for `fresh`, where no event
  follows. Only a scan that starts after the call measures what the call
  followed (an archive, say): pass `waitForSessionIds` (or `full`) and the
  `scanId` always names one, since such a request never gets `fresh` or
  `running_elsewhere` and, behind a scan under way, gets the scan queued
  after it. A plain refresh answered `already_running` or
  `running_elsewhere` names a scan that started before the call.
- `storage/usage/updated` carries a read response plus `eventId`, increasing
  per process, and `complete`. It is sent with `refreshing` when a scan starts
  and with the result when it ends, success or `failed`, to every connection
  that called a storage method. Both carry the scan's `scanId`, so a client
  waiting for the scan its refresh named takes the first non-`refreshing`
  event with that id as its answer, and ignores a late result of an earlier
  scan. Scan ids increase per process; a server before them sends none.

Clients show the storage UI (`/storage` in the CLI, the Storage panel and
low-disk alert in Desktop) only when the config view's
`experimentalEnableStorageUsage` is true; it follows the
`vibe_storage_usage` experiment and defaults to false, absent from an older
server. The methods themselves answer whatever the flag says.

Sizes are the space really used on disk: on macOS the private size of each
file, so a copy-on-write worktree counts only what it added; the template
counts its allocated size, once. A worktree not measured yet has `bytes:
null` and is left out of the totals.

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
  others. An interrupted subagent's turn ends for good, as `turn/interrupt`
  ends a root's: a later restart or `session/resume` does not carry it on.
  An app server that predates these methods answers `method_not_found`; a
  client treats that as nothing to stop.
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

`session/stop` does not end a running turn, and neither does closing the
channel, losing the client or the app server exiting. The turn stops where it
is and stays saved as running, as a crash leaves it, and the next
`session/resume` continues it, and the client receives `turn/started` for it:

- A model call the stop cut is sent again. Under a completion budget it is
  admitted like a new call, so a spent budget ends the turn with `token_limit`.
  A model call that had already failed is not sent again.
- A question to the user (`user_input` callback) or a tool approval
  (`approval` callback) open at the stop stays open: the resume response lists
  it in `activeCallbacks` with the session `blocked` on it, and the server
  sends it again as `callback/call` with the same `callbackId`. Its answer
  carries the turn on; the tool the approval gates runs only once approved. An
  approval is asked as the approval policy asks it now: its body
  (`requiredPermissions`, reason, display, input) is rebuilt, only the
  `callbackId` is kept. When the policy now decides the call without asking (a
  tool mode changed to allow or deny, or a grant covers it), nothing is asked:
  the decision applies and the stored approval is settled. An approval whose
  tool call Core no longer holds is closed as failed.
- A subagent whose turn was cut continues that turn when its root resumes, and
  its parent receives the usual subagent notification when the turn ends. A
  subagent the server cannot reopen does not prevent its root from resuming:
  its turn is recorded as failed, its parent's model is told it could not be
  restored after the restart and to spawn a new subagent, and its store is left
  on disk.
- A background process is not started again. A stop ends it, and the resumed
  model is told it stopped because the app server shut down or restarted. A
  crash leaves it unmanaged and possibly still running, and the model is told
  to check whether it runs before starting it again.

Only `turn/interrupt` ends a turn: a client that means to end it sends
`turn/interrupt` before `session/stop`. An interrupted turn is never resumed.

Tool/workspace/network/integration policy is enforced server-side. Rendering
metadata grants no permission. Public projections redact secrets and unsafe
internal errors; config views expose environment names rather than secret
values. Validate callback and client-tool results before execution-state
changes. Unknown protocol methods, fields and variants fail explicitly.

Boundary and lifecycle coverage lives in
[`tests/app_server`](../../tests/app_server),
[`client-e2e`](../../client-e2e),
[`tests/acp`](../../tests/acp) and source subprocess E2E tests.
