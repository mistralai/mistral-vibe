# 0012 Two-Phase Slash Command Execution

## Decision

Slash commands execute in two phases: an **instant phase** that runs
immediately (even while the agent or bash is busy) and an **idle phase**
that runs when the session is idle. Commands opt into the instant phase
with `side_channel=True` on the `Command` dataclass.

### Instant phase (side channel)

The `SideChannelController` is a single-slot runner that executes
allowlisted commands concurrently with the agent loop. It does not check
`agent_running` or `bash_task`. Only one side-channel command runs at a
time; a second submission while one is in flight is rejected with a toast.

The instant phase may:
- Execute the command entirely (e.g. `/exit`, `/status`, `/help`).
- Open a picker or modal and collect the user's selection
  (e.g. `/theme`, `/model`, `/thinking`, `/log-level`).
- Apply visual changes that are safe to apply immediately
  (e.g. `_apply_theme` sets the Textual theme reactive,
  `set_session_override` sets the session log level).
- Validate command arguments and show errors before queueing
  (e.g. `/log-level set garbage` shows an error immediately instead of
  surfacing at drain time).

### Idle phase (main queue)

Commands that need idle execution — either because they can't run while
busy (lifecycle operations like `/clear`, `/compact`, `/rewind`) or
because they persist config changes that require `require_idle` — are
enqueued on the main `MessageQueue` as a `COMMAND` item.

`COMMAND` items have two forms:
- **Text-only**: `content` is the raw command text, no payload. At drain
  time, `_dispatch_idle_input` replays it through normal classification.
- **Payload callback**: `content` is cosmetic (display only in the queue
  header), and `command_payload` is a `Callable[[], Awaitable[None]]`
  closure that executes the persist logic. At drain time,
  `_run_queued_command` calls the closure directly.

A `COMMAND` item whose handler opens a picker (e.g. `/mcp`, `/resume`)
blocks the drain until the user dismisses the picker and the input app
is restored. After running the command, the drain awaits
`await_input_app`, an `asyncio.Event`-backed gate that is cleared when a
picker is mounted and set when `_switch_to_input_app` returns. Commands
that don't open a picker set the event immediately, so the drain
proceeds. Without this gate, the drain would race past the picker and
run the next queued item while the user is still interacting with it.

The same `await_input_app` gate also blocks the drain at the top of each
drain cycle, so a side-channel picker that is still open when the agent
turn finishes (e.g. `/theme` opened while busy) holds the drain until
the user dismisses it. The drain task blocks on the event rather than
exiting, so it resumes on its own when the picker closes — no separate
restart trigger is needed.

Lifecycle commands that reset the conversation (`/clear`, `/new`) set
`flushes_pending=True` on the `Command` (and the queued item carries it
through). At drain time such a command drops any prompts queued before
it instead of running an LLM turn on the widgets the command is about to
tear down — `/clear` calls `_reset_message_widgets` (which removes those
widgets), so keeping them would run a turn on detached widgets.

The main queue only drains when the agent and bash are idle, so config
writes never hit `CONFLICT`. No retry, no dedup, no error-code checking
is needed.

### Two-phase flow

```
User types /theme while busy
  → side channel opens ThemePickerApp (instant phase)
  → user selects "nord"
  → _apply_theme("nord") (visual, instant)
  → _pending_theme = "nord"
  → enqueue_command("theme nord", partial(self._persist_theme, "nord"))
  → _switch_to_input_app()

  ─── agent turn finishes, queue drains ───

  → _run_queued_command("theme nord", payload)
  → await payload()  →  _persist_theme("nord")
      → config.update({"theme": "nord"})
      → config.reload(reload_runtime=False)
      → _pending_theme = None
```

When the same command runs from idle (normal path), the instant phase
and the idle phase execute sequentially — the picker opens, the user
selects, the command is enqueued, and the queue drains immediately.
Both phases run, each once, with no double-apply.

## Rationale

The main input queue drains only when the agent and bash are idle. That
is correct for prompts and bash (they are inputs to the agent loop), but
overly restrictive for slash commands that only touch UI state or need
to collect user input via a picker. The side channel runs these
immediately, without waiting.

Config persistence is deferred to the main queue because the app server
gates `config/patch` (and all config-writing RPCs) behind `require_idle()`
— an active turn rebuilds the backend, tool manager, skill manager, and
system prompt with `await` points that an in-flight turn could observe
partially. The main queue guarantees idle execution at drain time, so
`require_idle` never rejects. This eliminates the need for CONFLICT
handling, retry callbacks, and deduplication that a side-channel action
queue would require.

### Alternatives considered

**Side-channel action queue**: A deferred-idle queue on the
`SideChannelController` itself, where config writes that hit CONFLICT
are caught, enqueued, and retried at turn finalization. This requires
`AppServerResponseError` catching and `ProtocolErrorCode.CONFLICT`
checking in every config-writing handler, retry callbacks that return
`False` to signal "retry next flush", deduplication by key to avoid
stale writes, and a shared helper to avoid duplicating the
try/catch/defer/retry pattern. The main queue's `COMMAND` kind makes
all of this unnecessary.

**App-server-side queue**: Moving the deferred queue to the app server.
The server already owns the `SessionExecution` mutex and knows the exact
idle transition. However, the TUI needs synchronous feedback (did the
write apply? should the pending flag clear? should the picker refresh?),
and the server would need a new `DEFERRED` response status and a
server-to-client notification when deferred patches apply. Several writes
also trigger client-side side effects (`_reload_config()`,
`_apply_theme()`, voice manager sync) that would need a new async
coordination channel. The problem is currently TUI-only; other clients
(ACP, VS Code) receive raw `CONFLICT` and can handle it however they
want.

## Agent Guidance

- Mark a command `side_channel=True` only if its handler is safe to run
  concurrently with a streaming agent turn. Check whether the handler
  mutates agent-loop-shared state (backend, tool manager, skill manager,
  system prompt) or pushes a modal that conflicts with streaming output.
- Commands that persist config must enqueue a `COMMAND` item with a
  callable payload on the main queue. Never call `config.update` /
  `config.patch` / `config.set_thinking` directly from a side-channel
  handler — the session is busy and the write will hit `CONFLICT`.
- The instant phase and idle phase must be separate methods (e.g.
  `_apply_theme` for visual, `_persist_theme` for config write). The
  instant phase runs in the side-channel handler; the idle phase runs
  in the payload closure at drain time. Each runs once.
- Use `partial(self._persist_*, value)` as the payload callable. The
  closure captures the selection and clears `_pending_*` on success.
- If a function returns non-`None` (e.g. `config.patch` returns
  `ConfigPatchResponse`), wrap it in a `-> None` method before passing
  as a payload. The payload type is `Callable[[], Awaitable[None]]`.
- Use `_pending_*` fields and `_effective_*` properties for pickers that
  should show a pending selection if reopened before the switch takes
  effect.
- Validate command arguments in the instant phase, before queueing.
  Invalid arguments should show an error immediately, not surface at
  drain time.
- Commands that mutate session history (`/clear`, `/compact`, `/rewind`,
  `/resume`) or require a full config reload (`/reload`, `/leanstall`,
  `/unleanstall`, `/teleport`, `/remote-project`, `/retry`) go on the main
  queue as text-only `COMMAND` items — they can't run while busy and
  don't need a payload.
- A command that tears down conversation widgets (`/clear`, `/new`) must
  set `flushes_pending=True` so the drain drops prompts queued before
  it. Without it, the drain runs an LLM turn on widgets the command
  removes.
- A `COMMAND` that opens a picker (e.g. `/mcp`, `/resume`) blocks the
  drain automatically via `await_input_app`. The drain resumes once the
  picker's `on_*_cancelled`/`on_*_closed` handler calls
  `_switch_to_input_app`, which sets the `_input_app_ready` event. Don't
  add manual `await` waits for pickers in command handlers — the gate
  handles it.
- A deferred config persist must let exceptions propagate (the queue's
  `_run_queued_command` mounts an `ErrorMessage`), or — when run inline
  from a picker handler — be caught and surfaced there. Never swallow a
  config-write failure with a bare `except: log`; that reports success
  for a write that didn't happen.

## Flag To User When

- A side-channel command's handler calls `config.update` / `config.patch`
  / `config.set_thinking` / `config.reload` directly, bypassing the main
  queue.
- A command is marked `side_channel=True` but its handler pushes a modal
  or mutates state that an active turn would also touch.
- A lifecycle command (`/clear`, `/compact`, `/rewind`, `/resume`) or a
  reload command (`/reload`, `/leanstall`, `/unleanstall`, `/teleport`,
  `/remote-project`, `/retry`) is proposed for the side-channel allowlist.
- A payload callable returns non-`None` (should be wrapped in a
  `-> None` method).
- A `COMMAND` handler opens a picker but dismisses it without going
  through `_switch_to_input_app`, leaving `_input_app_ready` cleared and
  the drain stuck on `await_input_app`.
