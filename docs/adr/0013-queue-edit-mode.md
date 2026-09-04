# 0013 Queue Selection and Edit Mode for Queued Prompts

## Decision

When an agent turn is active and the app-server prompt queue is non-empty,
pressing Up enters queue selection mode. The newest queued prompt is
highlighted while the input remains focused and locked.

In selection mode:

- Up and Down move between queued prompts.
- Backspace or Delete removes the selected prompt through
  `app_server/session/turn/queue/remove`.
- Enter loads the selected prompt into the input for editing.
- Escape exits queue mode and restores the original draft.

Submitting an edit calls `app_server/session/turn/queue/replace`. The server
keeps the prompt's queue ID and FIFO position. Shell commands and slash commands
are never present because the app-server queue accepts user turns only.

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

Each queued prompt still has its own `UserMessage` widget, so queue selection can
navigate, edit, and remove prompts individually; edit and remove re-`replace`
the merged item with the remaining prompts. On resume the merged item is one
combined user history entry, so a resumed queue shows the prompts as a single
combined message.

## State ownership

`ChatInputBody` owns the selection and edit state machine. It receives three
callbacks from `VibeApp` through `ChatInputContainer`:

- `queue_edit_active_getter` reports whether an agent turn is active and queued
  prompts are available.
- `queue_items_getter` returns `(queue_index, content)` pairs in FIFO order.
- `queue_selected_index_getter` resolves the highlighted widget to its current
  queue index, or returns `None` after that prompt starts.

`VibeApp` owns the highlighted widget reference and the `queue-selected` CSS
class. `QueueController` maps app-server queue IDs to pending `UserMessage`
widgets and performs replace and remove requests. None of these classes owns
turn scheduling.

## Input locking

Selection mode sets `read_only=True`, hides the cursor, and intercepts the
navigation, edit, remove, and escape keys before `TextArea` handles them. Edit
mode unlocks the input and loads the selected prompt.

The app-level Escape binding is disabled while queue mode is active so Escape
can reach `ChatTextArea` and cancel the selection or edit.

## Promotion races

The app server can promote the oldest queued prompt while the user is selecting
or editing it. Numeric queue indices then shift, so the UI tracks the selected
prompt by widget identity.

- If a selected prompt starts, the next navigation re-reads the queue and moves
  selection to the nearest surviving prompt.
- If an edited prompt starts while the user is typing, the UI keeps the text and
  asks whether to submit it as a new prompt or discard it.
- The edit handler resolves the captured widget again after prompt preparation.
  If the prompt started during that await, it uses the same copy-on-write path.

## Rationale

Selection before editing avoids changing queued text accidentally and lets the
user delete a prompt without loading it into the input. Widget identity avoids
editing or deleting the wrong prompt when FIFO promotion changes numeric
indices.

Keeping the UI state separate from `HistoryManager` also avoids mixing
persistent input history with temporary app-server queue entries.

## Agent Guidance

- Queue selection applies only to app-server-backed prompts.
- Re-prepare edited prompts so file mentions and images match the new text.
- Resolve edits and removals by widget identity immediately before sending the
  app-server request.
- Keep `ChatInputBody` and `ChatInputContainer` independent of
  `QueueController`; use the callbacks above.
- Keep the `queue-selected` CSS class in `VibeApp`, which owns the selected
  widget reference.

## Flag To User When

- Shell commands or slash commands are added to queue selection.
- An edit bypasses `queue/replace` or a removal bypasses `queue/remove`.
- A cached numeric index is used after an `await` without re-resolving the
  selected widget.
