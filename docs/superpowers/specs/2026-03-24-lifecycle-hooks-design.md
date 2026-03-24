# Lifecycle Hooks for Mistral Vibe — Design Spec

## Goal

Add a configurable lifecycle hook system to Mistral Vibe that fires shell commands at key agent lifecycle events, passing structured JSON on stdin. This enables integration with external tools (specifically the Entire CLI external agent protocol) without modifying Vibe's core behavior.

## Approach: TelemetryClient-style (Direct Calls from AgentLoop)

### Why This Approach

Vibe's `AgentLoop` already uses a direct-call pattern for cross-cutting concerns. `TelemetryClient` is created in `__init__()` and called at lifecycle points:

| Existing TelemetryClient Call | Location in AgentLoop | Line |
|---|---|---|
| `send_new_session()` | `emit_new_session_telemetry()` | 315 |
| `send_tool_call_finished()` | `_handle_tool_response()` | 848 |
| `send_auto_compact_triggered()` | `_handle_middleware_result()` | 468 |

The new `HookManager` follows this identical pattern — created in `__init__()`, called at lifecycle points. This was chosen over an event-based observer approach because:

1. **Consumer-independent**: Hooks fire from inside AgentLoop regardless of consumer (TUI, programmatic, ACP, future GUI). No consumer code changes needed.
2. **Correct timing**: `pre_tool_use` fires after tool approval, right before execution — inside `_execute_tool_call()` at line 694 (after `_should_execute_tool` returns EXECUTE). An event-based approach would fire at `ToolCallEvent` time, which is before approval.
3. **Full context available**: At each hook point, all required data (tool_input, tool_response, session_id, prompt) is naturally in scope. No cross-event correlation needed.
4. **Minimal diff**: ~10 lines added to AgentLoop, 1 new file, 1 modified config file.
5. **Proven pattern**: Mirrors exactly how TelemetryClient integrates — reviewers can evaluate it as "same pattern, different purpose."

### Alternatives Considered

**Event-based Observer**: Wrap `act()` async generator with a HookObserver that intercepts events and fires hooks. Rejected because:
- Every consumer (programmatic.py, app.py, ACP) must independently wrap — if one forgets, hooks silently don't fire
- `ToolCallEvent` fires before tool approval (wrong timing for `pre_tool_use`)
- `ToolResultEvent` doesn't carry `tool_input` — observer must maintain state correlation across concurrent tool calls
- Larger change surface (6 files vs 3)

**Middleware**: Extend `ConversationMiddleware` with new hook points. Rejected because middleware only has `before_turn()` — it doesn't cover tool use events or session start/stop. Would require fundamentally changing the middleware contract.

---

## Detailed Design

### 1. Config Model — `vibe/core/config/_settings.py`

Add to existing `VibeConfig` Pydantic settings, loaded from the standard `.vibe/config.toml`:

```python
class HookEntry(BaseModel):
    command: str

class HooksConfig(BaseModel):
    session_start: list[HookEntry] = Field(default_factory=list)
    user_prompt_submit: list[HookEntry] = Field(default_factory=list)
    pre_tool_use: list[HookEntry] = Field(default_factory=list)
    post_tool_use: list[HookEntry] = Field(default_factory=list)
    turn_end: list[HookEntry] = Field(default_factory=list)
```

Added to `VibeConfig`:
```python
hooks: HooksConfig = Field(default_factory=HooksConfig)
```

**Config format** in `.vibe/config.toml` (follows Vibe's existing TOML convention):
```toml
[[hooks.session_start]]
command = "entire hooks mistral-vibe session-start"

[[hooks.user_prompt_submit]]
command = "entire hooks mistral-vibe user-prompt-submit"

[[hooks.pre_tool_use]]
command = "entire hooks mistral-vibe pre-tool-use"

[[hooks.post_tool_use]]
command = "entire hooks mistral-vibe post-tool-use"

[[hooks.turn_end]]
command = "entire hooks mistral-vibe turn-end"
```

**Why TOML arrays of tables (`[[hooks.*]]`)**: This is the standard TOML pattern for lists of objects, matching how Vibe already handles `[[providers]]`, `[[models]]`, and `[[mcp_servers]]`. It supports multiple commands per event naturally.

**Entire CLI compatibility**: The Entire CLI's `install-hooks` command writes this TOML programmatically. No separate `hooks.toml` file — uses the standard config path that Vibe already reads.

### 2. HookManager — `vibe/core/hooks.py` (NEW FILE)

```python
"""Lifecycle hook manager for Vibe.

Fires shell commands at key lifecycle events, passing structured JSON on stdin.
Hook commands run asynchronously and never block the agent loop.
"""
import asyncio
import json
import logging
from typing import Any

from vibe.core.config._settings import HooksConfig

logger = logging.getLogger(__name__)

HOOK_TIMEOUT_SECONDS = 30


class HookManager:
    """Manages lifecycle hook execution.

    Created in AgentLoop.__init__() with session config, session_id, and cwd.
    Public emit_*() methods are called at lifecycle points in AgentLoop.
    They enqueue hook subprocesses without awaiting. `drain()` is called at
    turn end to wait for all pending hook tasks to complete.
    """

    def __init__(self, config: HooksConfig, session_id: str, cwd: str) -> None:
        self._config = config
        self._session_id = session_id
        self._cwd = cwd
        self._tasks: set[asyncio.Task[None]] = set()

    def _base_payload(self, event_name: str) -> dict[str, Any]:
        return {
            "hook_event_name": event_name,
            "cwd": self._cwd,
            "session_id": self._session_id,
        }

    def _emit(self, event_name: str, extra: dict[str, Any] | None = None) -> None:
        """Enqueue all hooks registered for the given event."""
        hooks = getattr(self._config, event_name, [])
        if not hooks:
            return
        payload = self._base_payload(event_name)
        if extra:
            payload.update(extra)
        try:
            payload_bytes = json.dumps(payload, default=str).encode()
        except Exception:
            logger.exception("Failed to serialize hook payload for %r", event_name)
            return

        for hook in hooks:
            task = asyncio.create_task(self._run_hook(hook.command, payload_bytes))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_hook(self, command: str, payload: bytes) -> None:
        """Execute a single hook command as an async subprocess."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(input=payload),
                    timeout=HOOK_TIMEOUT_SECONDS,
                )
                if stderr:
                    logger.debug("Hook %r stderr: %s", command, stderr.decode(errors="replace"))
                if proc.returncode != 0:
                    logger.warning("Hook %r exited with code %d", command, proc.returncode)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("Hook %r timed out after %ds, killed", command, HOOK_TIMEOUT_SECONDS)
        except Exception:
            logger.exception("Failed to run hook %r", command)

    # ── Public API (called from AgentLoop) ──

    def emit_session_start(self) -> None:
        self._emit("session_start")

    def emit_user_prompt_submit(self, prompt: str) -> None:
        self._emit("user_prompt_submit", {"prompt": prompt})

    def emit_pre_tool_use(self, tool_name: str, tool_input: Any) -> None:
        self._emit("pre_tool_use", {"tool_name": tool_name, "tool_input": tool_input})

    def emit_post_tool_use(
        self,
        tool_name: str,
        tool_input: Any,
        tool_outcome: str,
        tool_response: Any | None = None,
        tool_error: str | None = None,
    ) -> None:
        self._emit("post_tool_use", {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_outcome": tool_outcome,
            "tool_response": tool_response,
            "tool_error": tool_error,
        })

    def emit_turn_end(self) -> None:
        self._emit("turn_end")

    async def drain(self) -> None:
        """Wait for all pending hook tasks to complete."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
```

**Important execution semantics**:

- `emit_session_start`, `emit_user_prompt_submit`, `emit_pre_tool_use`, `emit_post_tool_use`, and `emit_turn_end` enqueue work and return immediately
- only `drain()` awaits outstanding tasks
- `post_tool_use` fires for every terminal tool outcome: `success`, `failed`, `skipped`, and `cancelled`
- completed tasks are removed from the live task set as they finish, so long-running sessions do not accumulate unbounded finished task references
- payload serialization uses `json.dumps(default=str)` — non-serializable values (e.g., `Path` objects) are converted to strings rather than crashing; serialization failures are logged and the hook is skipped
- `turn_end` fires once per turn (per `act()` call), not once per session
- `turn_end` is the correct name for this lifecycle boundary. `stop` was rejected because it implies process exit or session shutdown, neither of which is true here

### 3. Integration into AgentLoop — `vibe/core/agent_loop.py`

Five integration points, each ~1-2 lines alongside existing code:

#### 3a. `__init__()` — Create HookManager (line ~215, after SessionLogger)

```python
# Existing:
self.session_logger = SessionLogger(config.session_logging, self.session_id)

# Add:
from vibe.core.hooks import HookManager
self._hook_manager = HookManager(
    config=config.hooks,
    session_id=self.session_id,
    cwd=str(Path.cwd()),
)
self._session_started = False
```

**Parallel**: Same location as `self.telemetry_client` creation (line 212).

#### 3b. `act()` — Fire session_start on first call (line ~341)

```python
async def act(self, msg: str) -> AsyncGenerator[BaseEvent]:
    # Add: fire session_start once
    if not self._session_started:
        self._hook_manager.emit_session_start()
        self._session_started = True

    self._clean_message_history()
    # ... existing code
```

**Why here**: `act()` is the public entry point. Session starts when the first prompt arrives. `emit_new_session_telemetry()` is called just before `act()` in both programmatic.py (line 63) and the TUI — same lifecycle moment.

#### 3c. `_conversation_loop()` — Fire user_prompt_submit (line ~516)

```python
yield UserMessageEvent(content=user_msg, message_id=user_message.message_id)

# Add:
self._hook_manager.emit_user_prompt_submit(user_msg)
```

**Why here**: Right after the user message is recorded, before the LLM turn begins. This matches the Entire CLI protocol's TurnStart event.

#### 3d. `_execute_tool_call()` — Fire pre/post_tool_use (all terminal branches)

```python
# After approval check succeeds (line 694):
self.stats.tool_calls_agreed += 1

# Add pre_tool_use:
self._hook_manager.emit_pre_tool_use(
    tool_call.tool_name, tool_call.args_dict
)

start_time = time.perf_counter()
# ... tool execution ...

# After successful execution (line ~728, after _handle_tool_response):
self._handle_tool_response(tool_call, text, "success", decision, result_dict, span=span)

# Add post_tool_use:
self._hook_manager.emit_post_tool_use(
    tool_call.tool_name,
    tool_call.args_dict,
    "success",
    tool_response=result_dict,
)
```

**Why after approval**: The spec requires `pre_tool_use` to fire when the tool is about to execute, not when the LLM requests it. Line 694 is after `_should_execute_tool()` returns EXECUTE — the tool has been approved and will run.

**Terminal outcome requirement**: `post_tool_use` must fire from every terminal branch of `_execute_tool_call()` with normalized outcome metadata:

- success: `tool_outcome="success"`, `tool_response=result_dict`, `tool_error=None`
- skip: `tool_outcome="skipped"`, `tool_response=None`, `tool_error=skip_reason`
- cancelled: `tool_outcome="cancelled"`, `tool_response=None`, `tool_error=cancel`
- failure: `tool_outcome="failed"`, `tool_response=None`, `tool_error=error_msg`

This keeps hook consumers aligned with real tool activity instead of only successful executions.

**`asyncio.CancelledError` handling** (line ~740): This branch re-raises after yielding the failure event, which means any code after the `raise` won't execute. `post_tool_use` must be emitted *before* the re-raise:

```python
except asyncio.CancelledError:
    cancel = str(get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED))
    self.stats.tool_calls_failed += 1
    yield self._tool_failure_event(
        tool_call, cancel, decision, cancelled=True, span=span
    )
    self._hook_manager.emit_post_tool_use(
        tool_call.tool_name, tool_call.args_dict,
        "cancelled", tool_error=cancel,
    )
    raise
```

This is safe because `emit_post_tool_use` does not await. It only enqueues an `asyncio.Task` and returns immediately, so cancellation cannot interrupt the enqueue step itself. The task lives in `self._tasks` independently of the calling coroutine's stack. When `CancelledError` propagates up to the `_conversation_loop` finally block, the turn-end hook is emitted and `drain()` waits for all pending tasks, including this one.

**Note**: `pre_tool_use` and `post_tool_use` are not always paired. Skipped and rejected tools only fire `post_tool_use` since the tool never began execution. The four terminal branches map to:

| Branch | `pre_tool_use` | `post_tool_use` |
|---|---|---|
| Approved → success | Yes | Yes (`outcome="success"`) |
| Approved → failure | Yes | Yes (`outcome="failed"`) |
| Approved → cancelled | Yes | Yes (`outcome="cancelled"`) |
| Skipped/rejected | No | Yes (`outcome="skipped"`) |

**Parallel**: `telemetry_client.send_tool_call_finished()` is called at line 848 in `_handle_tool_response()` — same lifecycle moment as `post_tool_use`, but hooks need broader outcome coverage than telemetry currently carries.

#### 3e. `_conversation_loop()` finally block — Fire turn_end and drain (line ~550)

```python
finally:
    await self._save_messages()
    # Add:
    self._hook_manager.emit_turn_end()
    await self._hook_manager.drain()
```

**Why in finally**: Ensures `turn_end` fires even if the conversation loop exits due to user cancellation, middleware STOP, or exception. Emitting the turn-end hook and then draining pending tasks guarantees all hooks complete before the turn ends.

**`turn_end` is a turn-end event, not a session-end event.** It fires once per `act()` call — at the end of each turn. In a multi-turn TUI session, the full lifecycle looks like:

```
session_start                              ← once, on first act()
  user_prompt_submit("prompt 1") → [tools] → turn_end    ← turn 1
  user_prompt_submit("prompt 2") → [tools] → turn_end    ← turn 2
  user_prompt_submit("prompt 3") → [tools] → turn_end    ← turn 3
(user exits TUI — no hook fires)
```

There is no `session_end` hook in this design. If `act()` is never called (user opens TUI and immediately exits), no hooks fire at all — no meaningful session occurred. A `session_end` hook could be added in the future without breaking changes, firing from the TUI exit handler and `programmatic.py` cleanup.

---

## Hook Payload Format

Every hook receives a JSON object on stdin. The payload always includes `hook_event_name`, `cwd`, and `session_id`. Event-specific fields are added per hook type.

### session_start
```json
{
  "hook_event_name": "session_start",
  "cwd": "/path/to/repo",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### user_prompt_submit
```json
{
  "hook_event_name": "user_prompt_submit",
  "cwd": "/path/to/repo",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "prompt": "Fix the login bug"
}
```

### pre_tool_use
```json
{
  "hook_event_name": "pre_tool_use",
  "cwd": "/path/to/repo",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "tool_name": "write_file",
  "tool_input": {"file_path": "/src/main.py", "content": "..."}
}
```

### post_tool_use
```json
{
  "hook_event_name": "post_tool_use",
  "cwd": "/path/to/repo",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "tool_name": "write_file",
  "tool_input": {"file_path": "/src/main.py", "content": "..."},
  "tool_outcome": "success",
  "tool_response": {"result": "File written successfully"},
  "tool_error": null
}
```

### post_tool_use (failure)
```json
{
  "hook_event_name": "post_tool_use",
  "cwd": "/path/to/repo",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "tool_name": "bash",
  "tool_input": {"command": "bad command"},
  "tool_outcome": "failed",
  "tool_response": null,
  "tool_error": "<tool_error>bash failed: ...</tool_error>"
}
```

### turn_end
```json
{
  "hook_event_name": "turn_end",
  "cwd": "/path/to/repo",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

## Execution Requirements

| Requirement | Implementation |
|---|---|
| **Non-blocking** | `asyncio.create_task()` — hook runs concurrently, never blocks agent loop |
| **stdin pipe** | JSON payload passed via `proc.communicate(input=payload)` |
| **No stdout capture** | `stdout=asyncio.subprocess.DEVNULL` |
| **stderr logging** | Logged at DEBUG level to Vibe's logger |
| **30s timeout** | `asyncio.wait_for(..., timeout=30)` then `proc.kill()` and `await proc.wait()` |
| **Error isolation** | All exceptions caught and logged — never crashes Vibe |
| **Payload serialization safety** | `json.dumps(default=str)` with try/except — non-serializable values become strings, serialization failures are logged and skipped |
| **Bounded task tracking** | Live tasks stored in a set and removed on completion |
| **Drain at turn end** | `emit_turn_end()` enqueues the final hook, then `drain()` awaits all pending tasks via `asyncio.gather()` |

---

## Files Changed

| File | Change | Lines |
|---|---|---|
| `vibe/core/hooks.py` | **NEW** — HookManager class | ~120 |
| `vibe/core/config/_settings.py` | Add HookEntry, HooksConfig, hooks field to VibeConfig | ~15 |
| `vibe/core/agent_loop.py` | Create HookManager, 5 `emit_*()` calls plus `drain()` in the finally path | ~18 |

**Total**: ~150 lines, 3 files (1 new, 2 modified).

---

## Testing Strategy

### Unit Tests (TDD — write first)

Test `HookManager` in isolation:

1. **Payload correctness**: Verify each `emit_*()` method produces the correct JSON payload with all required fields
2. **No hooks configured**: Verify `_emit()` is a no-op when no hooks are registered (no subprocess spawned)
3. **Multiple hooks per event**: Verify all registered commands fire for a single event
4. **Timeout handling**: Verify hooks that exceed 30s are killed
5. **Error isolation**: Verify a failing hook command doesn't raise — logged and swallowed
6. **stderr logging**: Verify hook stderr output is logged at debug level
7. **bounded task tracking**: Verify completed tasks are removed from the live task set
8. **turn_end drains tasks**: Verify `emit_turn_end()` plus `drain()` waits for all pending hook tasks before returning

### Integration Tests

Test hooks fire from AgentLoop at correct lifecycle points:

1. **session_start fires once**: Multiple calls to `act()` only fire session_start on the first call
2. **user_prompt_submit fires per prompt**: Each `act()` call fires with the prompt text
3. **pre_tool_use timing**: Fires after approval, not for skipped/rejected tools
4. **post_tool_use covers all terminal outcomes**: Fires with normalized outcome metadata for success, skip, cancel, and failure
5. **turn_end fires in finally**: Fires even when conversation loop exits early (middleware STOP, cancellation)

### E2E Tests

Split end-to-end coverage by entrypoint:

1. **TUI e2e**: Spawn Vibe via `pexpect` with hook config pointing to a test script that dumps payloads to temp files
2. **Non-TUI flow**: Exercise programmatic mode through a focused non-`pexpect` path and assert the same hook side effects
3. **Lifecycle validation**: Verify session_start → user_prompt_submit → (tool events if applicable) → turn_end
4. **Payload validation**: Verify parseable JSON and expected fields in both flows

---

## Entire CLI Compatibility

The Entire CLI external agent binary (`entire-agent-mistral-vibe`) maps hook events to protocol event types:

| Vibe Hook Event | Entire Protocol Event | Type Value |
|---|---|---|
| `session_start` | SessionStart | 1 |
| `user_prompt_submit` | TurnStart | 2 |
| `pre_tool_use` | *(no protocol event — used for checkpoint capture)* | — |
| `post_tool_use` | *(no protocol event — used for file change detection)* | — |
| `turn_end` | TurnEnd | 3 |

The binary reads JSON from stdin, extracts fields, and constructs protocol events. The `session_id` from Vibe's hook payload becomes the session identifier in Entire's checkpoint system.

The Entire hook verb for this event should be `turn-end` to match the Vibe-native hook name `turn_end`. If backward compatibility with older prototype docs is needed, Entire may accept `stop` as an alias, but Vibe's native hook vocabulary should use `turn_end` because that is the actual lifecycle boundary being emitted.

Session transcripts are found at `~/.vibe/logs/session/session_YYYYMMDD_HHMMSS_<id[:8]>/messages.jsonl` — the binary uses the session_id to locate them.

---

## Interactive Architecture Diagram

An interactive comparison playground is available at `docs/hooks-architecture-comparison.html` showing:
- **Current Architecture** tab — existing system without hooks
- **Approach A** tab — this TelemetryClient-style design
- **Approach B** tab — the rejected event-based observer alternative

Hover over any node for implementation details.
