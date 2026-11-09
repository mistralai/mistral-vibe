# 0013 Queue Selection and Edit Mode for Queued Prompts

## Decision

When an agent turn is active and the app-server prompt queue is non-empty,
pressing Up enters queue selection mode. The newest queued prompt is
highlighted while the input remains focused and locked.

In selection mode:

- Up and Down move between queued prompts.
- Backspace or Ctrl+C removes the selected prompt through
  `session/turn/queue/remove`.
- Enter loads the selected prompt into the input for editing.
- Escape exits queue mode and restores the original draft.

Submitting an edit calls `session/turn/queue/replace`. The server
keeps the prompt's queue ID and FIFO position. Saving the edit also
exits queue selection mode entirely: the highlight, edit state, and
consumed-edit state are cleared and the pre-queue draft is restored,
so the normal bindings resume — a later empty Enter steers the running
turn and Escape interrupts it. Escape while editing still discards
the edit and returns to selection with its controls hint. Shell
commands and slash commands are never present because the app-server
queue accepts user turns only.

When the agent is busy and the queue is empty, Up falls back to normal history
navigation.

## Merged delivery

The CLI keeps at most one app-server queue item for everything queued during a
single generation. The first prompt enqueued while busy creates the item; each
later prompt is folded into it with `app_server/session/turn/queue/replace`, so
the server promotes one already-merged turn and the agent receives every queued
prompt together. This restores the pre-turn-queue "send them all at once"
behaviour without changing the server queue, which still promotes one item at a
time — the desktop app keeps turn-by-turn delivery.

Each queued prompt still has its own transcript entry, so queue selection can
navigate, edit, and remove prompts individually; edit and remove re-`replace`
the merged item with the remaining prompts. On resume the merged item is one
combined user history entry, so a resumed queue shows the prompts as a single
combined message.

## Steering the merged queue

While an active turn is running, empty Enter or Ctrl+Enter sends the merged
queued block into that turn as steering. Empty Enter on a paused queue resumes
normal queue delivery instead.

For Unified Harness sessions, `QueueController` calls
`session/turn/queue/steer` with only the queue item ID and expected active turn
ID. The server uses the content already stored in the queue. The client does
not remove the item, call ordinary `turn/steer`, or rebuild the item after an
error.

The queued entries remain pending and keep their positions while the request
is in flight. A matching user `history/entryAdded` event with
`source = turn_steer` marks them sent in place. Because the event stream is
processed sequentially, output accepted before that history entry stays above
the steered message and later output stays below it, including while subagents
are running.

Only one atomic queued steer may be in flight. Later submissions wait for it to
settle, and edit or remove actions cannot overtake it. If the connection drops,
the next authoritative session snapshot resolves whether the message entered
history, remained queued, or started through normal promotion. The client never
re-enqueues an uncertain request.

## State ownership

`QueueController` (`vibe/cli-rust/src/message_queue.rs`) owns the queued
prompts and the selection and edit state machine: the highlighted prompt, the
edit and consumed-edit state, and the draft saved when queue mode opened. It maps
app-server queue IDs to pending transcript entries and performs replace,
remove, and Unified queued-steer requests. The chat input and transcript only
render that state. None of them owns turn scheduling or reconstructs
server-owned queue content.

## Input locking

Selection mode locks the chat input read-only, hides the caret, and intercepts
the navigation, edit, remove, and escape keys before any editing key handles
them. Edit mode unlocks the input and loads the selected prompt.

While queue mode is active, the queue key handler runs before the app-level
Escape binding, so Escape cancels the selection or edit instead of
interrupting the turn.

## Key hints

While queue selection or editing is active and the composer receives keys, its
controls replace the loading line's turn hints (interrupt, steer, cancel last
queued message) for exactly as long as the mode lasts. Hints
are derived from the same state the keys dispatch on at render time, never shown
on a timer: a turn hint is offered only when its key would perform that action
(for example, Enter to steer only on an empty, steerable input).

## Promotion races

The app server can promote the oldest queued prompt while the user is selecting
or editing it. Numeric queue indices then shift, so the UI tracks the selected
prompt by its transcript entry ID.

- If a selected prompt starts, the next navigation re-reads the queue and moves
  selection to the nearest surviving prompt.
- If an edited prompt starts while the user is typing, the UI keeps the text and
  asks whether to submit it as a new prompt or discard it.
- Saving an edit resolves the selected entry ID again before sending. If the
  prompt has started by then, it uses the same copy-on-write path.

## Rationale

Selection before editing avoids changing queued text accidentally and lets the
user delete a prompt without loading it into the input. Entry identity avoids
editing or deleting the wrong prompt when FIFO promotion changes numeric
indices.

Keeping the UI state separate from the input history also avoids mixing
persistent input history with temporary app-server queue entries.

## Agent Guidance

- Queue selection applies only to app-server-backed prompts.
- Re-prepare edited prompts so file mentions and images match the new text.
- Resolve edits and removals by transcript entry ID immediately before sending
  the app-server request.
- Keep queued entries pinned until the matching steering history entry arrives;
  do not infer steering from assistant output or subagent completion.
- Reconcile an uncertain queued-steer request from the session snapshot; never
  re-enqueue it from client state.
- Keep queue selection and edit state in `QueueController`; the chat input and
  transcript only render it.
- Derive key hints from the predicates their key handlers use; do not show
  queue-mode hints through timed notices.

## Flag To User When

- Shell commands or slash commands are added to queue selection.
- An edit bypasses `queue/replace` or a removal bypasses `queue/remove`.
- Unified queued steering is split into client-side remove and steer requests.
- A delivery heuristic depends on assistant messages or subagent completion.
- A cached numeric index is used after the queue may have changed without
  re-resolving the selected entry.
