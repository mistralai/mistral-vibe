# 0013 Queue Selection and Edit Mode for Queued Messages

## Decision

When the agent is busy and the input queue is non-empty, pressing Up
enters **queue selection mode**: the last queued item is visually
highlighted (CSS `queue-selected` class) and the input retains focus but
does not load the item text. The user can navigate Up/Down across all
queued items (prompts and bash), delete the selected item with
Backspace/Delete, or press Enter to transition into **edit mode** for
that item.

In edit mode, the item's text is loaded into the input field. Bash
items load with their `!` prefix restored so the edit happens in bash
mode. Enter commits the edit: for prompts it re-prepares @mentions and
images, for bash it refreshes the rendered command display, both via
the generalized `QueueController.update_item()`. Escape reverts the edit
and returns to selection mode.

When the agent is busy and the queue is empty, Up falls back to the
existing copy-on-write history navigation.

Selection and edit modes are implemented as a state machine in
`ChatInputBody`, not by extending `HistoryManager`. `ChatTextArea`
intercepts keys at the `_on_key` level via a `_queue_selection_active`
flag (same pattern as `feedback_active`), posting
`QueueSelectionPrevious`/`Next`/`Enter`/`Remove`/`Exit` messages that
the body handles to manage the selection cursor and mode transitions.

The mode is gated by three callbacks injected into `ChatInputBody` via
`ChatInputContainer`:

- `queue_edit_active_getter` — returns `True` when the agent is busy,
  the queue is non-empty, and the queue is not draining.
- `queue_items_getter` — returns `list[tuple[int, QueuedItemKind, str]]`
  of `(queue_index, kind, content)` triples for all items in the queue
  (prompts and bash), in queue order.
- `queue_selected_index_getter` — returns the live queue index of the
  highlighted widget, or `None` once it has been consumed by the drain
  (see Drain race handling).

On Enter from selection mode, `ChatInputBody` transitions to edit mode
and loads the item text. On Enter in edit mode, it posts
`QueueEditSubmitted(value, kind)`, which `VibeApp` handles by resolving
the item by widget identity (not by a stored index) and calling
`QueueController.update_item()`.

On Backspace/Delete in selection mode, the body posts
`QueueRemoveRequested()`, which `VibeApp` handles by resolving the
highlighted item by widget identity and calling
`QueueController.pop_at(index)`. Selection moves to the next item
(newer).

## Input locking during selection mode

In selection mode, the input must be non-interactive: no cursor, no
typing. `ChatInputBody` sets `read_only=True`, `cursor_blink=False`,
and `show_cursor=False` on the `ChatTextArea` when entering selection
mode, and restores them when entering edit mode or exiting queue mode
entirely.

The selection-mode key intercepts in `ChatTextArea._on_key` run before
`super()._on_key()`, so they still fire when `read_only=True` (the
parent returns early for non-intercepted keys, which is exactly the
desired behavior — typing is blocked, navigation keys work).

## Escape key routing

The app has a priority binding `Binding("escape", "interrupt", ...)` at
the App level that intercepts Escape before it reaches `ChatTextArea`.
This would prevent `QueueSelectionExit` / `QueueEditCancelled` from
firing during queue modes.

`VibeApp.check_action` guards the `interrupt` action: when
`body.in_queue_mode` is true (selection or edit mode active), the
binding is disabled so Escape falls through to `ChatTextArea`, which
posts the appropriate queue-mode message. The container lookup uses
`query` (not `query_one`) so it cannot raise `NoMatches` if the action
fires mid-lifecycle. This follows the same pattern as the existing
guard for config-modal screens.

## Drain race handling

The drain engine runs as a background task and can consume items while
the user is in selection or edit mode. Items are consumed from the front
(`pop_first`), which shifts the surviving items to lower absolute
indices — so a cached `queue_index` is unreliable. The state machine
tracks the highlighted item by **widget identity** instead:

- The app keeps `_queue_selected_widget` (the widget the highlight is
  on) and resolves its live index via `_queue_index_of_widget(widget)`,
  which scans the queue's widgets by identity (`is`) and returns `None`
  once the widget has been removed by the drain.

- **Selection mode + item consumed**: on each navigation the body's
  `_resync_selection` re-snapshots the live queue and re-derives the
  cursor. If the highlighted widget is gone (`live_index is None`) it
  clamps to the next surviving item and re-posts the scroll so the app
  re-syncs `_queue_selected_widget`. If no items remain, selection mode
  exits and the original input text is restored.

- **Edit mode + item consumed while typing**: the body detects the
  consumption via `queue_selected_index_getter()` returning `None`, sets
  `_queue_edit_consumed = True`, and posts an `InlineNoticeRequested`
  ("This message was already processed — press Enter to submit as new,
  or Escape to discard"). The user is not interrupted mid-keystroke. On
  Enter, the body posts `QueueEditConsumed(value, kind)`, which the app
  enqueues as a **fresh item preserving its kind** (bash stays bash so
  shell text is never sent to the model as a prompt). On Escape, the
  edit is discarded and selection mode is restored.

As defense-in-depth, the app's `QueueEditSubmitted` handler also
re-resolves the item's index by widget identity after the prepare
await (the body returns to selection mode before the await, so the
highlight can move); if the captured widget was consumed, the edit
falls back to the same copy-on-write enqueue.

## Rationale

**Why a separate selection phase before edit?**
Directly loading text into the input on Up (the v1 design) makes it
ambiguous whether the user wants to edit or to compose a new prompt
based on a queued item. The selection phase provides a clear visual
highlight and a deliberate Enter press to enter edit mode, preventing
accidental edits. It also enables deletion without entering edit mode.

**Why a state machine in ChatInputBody, not HistoryManager?**
`HistoryManager` reads and writes a persisted file on disk; queue items
are ephemeral in-memory `QueuedItem` instances with a different shape
(`prepared_prompt`, `skill_name`). Mixing the two in one navigation
state would couple the persisted-history layer to the transient-queue
layer.

**Why callbacks instead of a direct queue reference?**
`ChatInputBody` and `ChatInputContainer` are reusable widget components
that must not import `QueueController` or `MessageQueue`. The callbacks
keep the dependency arrow pointing from `VibeApp` (which owns both the
input widget and the queue) into the widget, never the reverse.

**Why suppress HistoryReset during selection and edit?**
`HistoryReset` fires from `ChatTextArea.on_text_area_changed` whenever
the text changes and the widget is not in `_navigating_history`. In
queue modes the user edits freely, and that would reset the history
cursor and wipe the saved draft. The `_queue_selection_active` and
`_queue_edit_active` flags suppress the reset.

**Why `dataclasses.replace` for QueuedItem updates?**
`QueuedItem` is a frozen dataclass with `slots=True`. `replace` creates
a new instance with updated fields while preserving `kind`,
`skill_name`, and other fields that should not change during an edit.

**Why inline notice for the consumed-item case?**
Interrupting with a modal while the user is mid-keystroke is jarring.
The inline notice lets the user decide naturally: Enter submits as new,
Escape discards. This is less disruptive than a dialog.

**Why busy + empty queue falls back to history?**
When the queue is empty, there is nothing to select or edit. The user
is still typing a new prompt to enqueue, and Up-on-history
(copy-on-write) is the established behavior for that flow.

## Agent Guidance

- Gate queue selection strictly on `_is_queue_edit_active`: busy (agent
  or bash running), queue non-empty, and not draining. Draining means
  items are being consumed; selecting or editing a mid-drain item
  would race.
- All queue items (prompts and bash) are selectable and deletable. Both
  prompts and bash are editable (`_EDITABLE_QUEUE_KINDS`); bash loads with
  `!` and its edit refreshes the rendered command display. Slash commands
  are only deletable — their handlers entangle too much UI state (pickers,
  agents, history) to safely rewrite mid-flight.
- Re-prepare the prompt on edit submit (`_prepare_prompt_or_abort`) so
  @mentions and image paths are resolved against the edited text. Bash
  edits skip this (no @mentions) and store the raw command.
- Keep `ChatInputBody` and `ChatInputContainer` free of imports from
  `vibe.cli.textual_ui.message_queue`. `QueuedItemKind` lives in its own
  `vibe.cli.textual_ui.queue_kinds` module so the widgets can reference it
  without depending on the file that owns `QueueController`. The
  queue-edit callbacks are the only other contact surface.
- The `queue-selected` CSS class is toggled by `VibeApp` in the
  `QueueSelectionScroll` handler, not by the body. The app owns the
  `_queue_selected_widget` reference and clears it on exit via
  `QueueModeExited`.
- `pop_at(index)` on `QueueController` removes both the data item and
  the widget from the DOM, keeping `_items` and `_widgets` in lockstep.

## Flag To User When

- Queue selection intercepts Up/Down when the queue is empty or the
  agent is idle (it should not).
- `ChatInputBody` or `ChatInputContainer` imports `MessageQueue`,
  `QueueController`, `QueuedItem`, or any queue-internal type directly.
  (`QueuedItemKind` from `queue_kinds` is allowed — it is a value enum
  with no queue coupling.)
- A queued item is edited without re-preparing the prompt (stale
  @mentions or images).
- The drain engine treats edited items differently from non-edited ones
  (it should not — the feeding behavior is unchanged).
- The `queue-selected` CSS class is toggled from inside `ChatInputBody`
  instead of from `VibeApp`.
