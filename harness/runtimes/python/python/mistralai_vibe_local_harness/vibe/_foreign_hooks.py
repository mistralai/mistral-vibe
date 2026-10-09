"""Foreign-command hook executors for user-facing hooks.

A *foreign* hook is a user command declared in ``hooks.toml``. This module builds
the per-point handler that the Runtime registers: it assembles the point's JSON
event, spawns the command in the session cwd, enforces a timeout, and maps the
command's stdout to the point's typed result.

A handler **never raises for an execution failure**. Any spawn error, timeout,
non-zero exit, or invalid JSON is mapped to the point's fail action, never to an
exception — raising would surface as ``hook_pipeline_failed`` regardless of the
point's policy. The fail action per point:

* ``pre_tool`` — ``strict`` fails closed to *deny*, else open to *allow*.
* ``post_tool`` — ``strict`` blanks the tool result, else passes it through.
* ``post_agent`` — always *accepts* the turn (no blocking mode), so a broken
  review hook can never wedge completion; ``strict`` is accepted for a uniform
  builder API but does not change this.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import contextlib
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from mistralai_vibe_local_harness.protocol import (
    RustAcceptCandidate,
    RustCompletionHookAccept,
    RustCompletionHookInput,
    RustCompletionHookRetry,
    RustContentBlock,
    RustExternalToolCall,
    RustHookSkip,
    RustPostAgentTurnHookResult,
    RustPostToolCallHookInput,
    RustPostToolCallHookResult,
    RustPostToolCallOutput,
    RustPreToolCallContinue,
    RustPreToolCallHookInput,
    RustPreToolCallHookResult,
    RustTextContentBlock,
    RustToolFailureResult,
    RustToolResult,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject
from mistralai_vibe_local_harness.vibe._hook_matcher import (
    match_predicate,
    qualified_tool_name,
    selects_tool,
)
from mistralai_vibe_local_harness.vibe._local_actions import (
    HookContext,
    HookNoticeData,
    PostAgentTurnHookHandler,
    ToolScopedHook,
)
from mistralai_vibe_local_harness.vibe._sandbox_helper import kill

_DEFAULT_TIMEOUT_S = 60.0
_HOOK_EVENT_NAME = "pre_tool"
_POST_TOOL_EVENT_NAME = "post_tool"
_POST_AGENT_EVENT_NAME = "post_agent"
_DEFAULT_DENY_REASON = "Blocked by a pre_tool hook."
_STRICT_FAILURE_REASON = "A strict pre_tool hook failed and denied the call."
_POST_TOOL_DENY_REASON = "The tool result was blocked by a post_tool hook."
_POST_AGENT_DENY_REASON = "A post_agent hook asked the agent to revise its response."

__all__ = [
    "build_post_agent_turn_handler",
    "build_post_tool_call_handler",
    "build_pre_tool_call_handler",
]


@dataclass(frozen=True, slots=True)
class _HookNotice:
    # A hook's ``hook_completed`` line. A passthrough run yields ``None`` instead, so no
    # line is shown unless the hook changed something.
    content: str
    status: str


async def _emit_completed(
    context: HookContext,
    *,
    name: str,
    scope: str,
    tool_call_id: str | None,
    notice: _HookNotice | None,
) -> None:
    if notice is None or context.emit_hook_notice is None:
        return
    await context.emit_hook_notice(
        HookNoticeData(
            kind="hook_completed",
            scope=scope,
            tool_call_id=tool_call_id,
            hook_name=name,
            status=notice.status,
            content=notice.content,
        )
    )


def _tool_scoped[I, R](
    handler: Callable[[I, HookContext], Awaitable[R]], match: str | None
) -> ToolScopedHook[I, R]:
    predicate = match_predicate(match)
    return ToolScopedHook(
        run=handler, selects=lambda name: selects_tool(predicate, name)
    )


def _stdin_payload(
    hook_input: RustPreToolCallHookInput, context: HookContext
) -> dict[str, Any]:
    tool_call = hook_input.tool_call
    return {
        **_base_payload(context),
        "hook_event_name": _HOOK_EVENT_NAME,
        "tool_name": qualified_tool_name(tool_call.call),
        "tool_call_id": tool_call.call_id,
        "tool_input": _author_tool_input(tool_call.call),
    }


class _HookCommandError(RuntimeError):
    # Carries the legacy-style failure reason (stderr, stdout, timeout, exit code) so a
    # fail-open run can surface it instead of vanishing.
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _execution_reason(error: Exception) -> str:
    if isinstance(error, _HookCommandError):
        return error.reason
    return f"hook failed: {error}"


def _renamed(arguments: JsonObject, source: str, target: str) -> JsonObject:
    if source not in arguments:
        return arguments
    return {
        target: arguments[source],
        **{key: value for key, value in arguments.items() if key != source},
    }


def _search_replace_author_arguments(arguments: JsonObject) -> JsonObject:
    # The legacy edit shape carries a single search/replace pair at the top level. A
    # multi-block runtime payload cannot be represented in it, so it is passed through
    # untouched rather than silently dropping the extra blocks.
    content = arguments.get("content")
    if not (
        isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict)
    ):
        return arguments
    block = content[0]
    return {
        **{key: value for key, value in arguments.items() if key != "content"},
        "old_string": block.get("old_str", ""),
        "new_string": block.get("new_str", ""),
        "replace_all": block.get("replace_all", False),
    }


# Runtime argument names to the ones the CLI's tools — and therefore hooks written
# against the legacy runtime — use. Each translator renames keys on a copy of the full
# argument set; it must not rebuild a subset, or hooks silently lose fields the Core
# still executes (offset/limit on read_file, timeout_seconds on bash).
_AUTHOR_ARGUMENTS: Mapping[str, Callable[[JsonObject], JsonObject]] = {
    "file_system.read_file": lambda arguments: _renamed(arguments, "path", "file_path"),
    "file_system.write_file": lambda arguments: _renamed(
        arguments, "path", "file_path"
    ),
    "file_system.search_replace": _search_replace_author_arguments,
    "file_system.bash": lambda arguments: _renamed(
        arguments, "timeout_seconds", "timeout"
    ),
}


def _read_file_runtime_arguments(arguments: JsonObject) -> JsonObject | None:
    if "file_path" not in arguments or "path" in arguments:
        return None
    return _renamed(arguments, "file_path", "path")


def _write_file_runtime_arguments(arguments: JsonObject) -> JsonObject | None:
    if "file_path" not in arguments or "path" in arguments:
        return None
    return _renamed(arguments, "file_path", "path")


def _bash_runtime_arguments(arguments: JsonObject) -> JsonObject | None:
    if "timeout" not in arguments or "timeout_seconds" in arguments:
        return None
    return _renamed(arguments, "timeout", "timeout_seconds")


def _search_replace_runtime_arguments(arguments: JsonObject) -> JsonObject | None:
    if "old_string" not in arguments and "new_string" not in arguments:
        return None
    return {
        **{
            key: value
            for key, value in arguments.items()
            if key not in {"old_string", "new_string", "replace_all", "content"}
        },
        "content": [
            {
                "old_str": arguments.get("old_string", ""),
                "new_str": arguments.get("new_string", ""),
                "replace_all": arguments.get("replace_all", False),
            }
        ],
    }


# The reverse direction, for a pre_tool rewrite: a hook speaks the author-facing names
# and the Core executes runtime names. ``None`` means the rewrite already looks
# runtime-shaped and passes through untouched.
_RUNTIME_ARGUMENTS: Mapping[str, Callable[[JsonObject], JsonObject | None]] = {
    "file_system.read_file": _read_file_runtime_arguments,
    "file_system.write_file": _write_file_runtime_arguments,
    "file_system.search_replace": _search_replace_runtime_arguments,
    "file_system.bash": _bash_runtime_arguments,
}


def _author_tool_input(call: RustExternalToolCall) -> JsonObject:
    translate = _AUTHOR_ARGUMENTS.get(qualified_tool_name(call))
    if translate is None:
        return dict(call.arguments)
    return translate(dict(call.arguments))


def _runtime_tool_input(
    call: RustExternalToolCall, rewritten: JsonObject
) -> JsonObject:
    translate = _RUNTIME_ARGUMENTS.get(qualified_tool_name(call))
    if translate is None:
        return dict(rewritten)
    return translate(dict(rewritten)) or dict(rewritten)


def _base_payload(context: HookContext) -> dict[str, Any]:
    # The legacy payload's shared session fields, emitted identically by all three
    # builders so they cannot drift between points.
    return {
        "session_id": context.session_id,
        # The session's storage root; the durable journal lives under it, which is
        # what per-session audit and transcript-reading hooks key off.
        "transcript_path": str(context.session_root) if context.session_root else "",
        "cwd": str(context.config.workspace.cwd),
        "parent_session_id": context.parent_session_id,
    }


def _system_message(parsed: dict[str, Any]) -> str | None:
    message = parsed.get("system_message")
    if isinstance(message, str) and message:
        return message
    return None


def _text_reason(text: str) -> list[RustContentBlock]:
    block: RustContentBlock = RustTextContentBlock(text=text)
    return [block]


def _continue(effective_arguments: JsonObject) -> RustPreToolCallHookResult:
    return RustPreToolCallHookResult(
        output=RustPreToolCallContinue(effective_arguments=dict(effective_arguments))
    )


def _skip(reason: str) -> RustPreToolCallHookResult:
    return RustPreToolCallHookResult(output=RustHookSkip(reason=_text_reason(reason)))


def _fail(
    strict: bool,
    original_arguments: JsonObject,
    *,
    tool_name: str,
    failure_reason: str | None = None,
) -> tuple[RustPreToolCallHookResult, _HookNotice | None]:
    if strict:
        return (
            _skip(failure_reason or _STRICT_FAILURE_REASON),
            _HookNotice(f"Denied tool {tool_name!r} (strict)", "error"),
        )
    return (
        _continue(original_arguments),
        _HookNotice(failure_reason or "hook failed", "warning"),
    )


def _interpret_stdout(  # noqa: PLR0911 - one early return per stdout shape
    stdout: str,
    original_arguments: JsonObject,
    *,
    strict: bool,
    tool_name: str,
    call: RustExternalToolCall,
) -> tuple[RustPreToolCallHookResult, _HookNotice | None]:
    """Map exit-0 stdout to a result + notice. Empty stdout allows; invalid JSON fails."""
    text = stdout.strip()
    if not text:
        return _continue(original_arguments), None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        return _fail(
            strict,
            original_arguments,
            tool_name=tool_name,
            failure_reason=f"stdout did not match the hook response schema: {error}",
        )
    if not isinstance(parsed, dict):
        return _fail(
            strict,
            original_arguments,
            tool_name=tool_name,
            failure_reason="stdout did not match the hook response schema: not an object",
        )

    if parsed.get("decision") == "deny":
        reason = parsed.get("reason")
        return (
            _skip(str(reason) if reason else _DEFAULT_DENY_REASON),
            _HookNotice(f"Denied tool {tool_name!r}", "error"),
        )

    system_message = _system_message(parsed)

    # Allow (any non-"deny" decision, including absent/unknown), optionally with
    # rewritten arguments via hook_specific_output.tool_input.
    hook_output = parsed.get("hook_specific_output")
    if isinstance(hook_output, dict):
        rewritten = hook_output.get("tool_input")
        if isinstance(rewritten, dict):
            return (
                _continue(_runtime_tool_input(call, rewritten)),
                _HookNotice(
                    system_message or f"Rewrote tool_input for {tool_name!r}", "warning"
                ),
            )
    if system_message:
        return _continue(original_arguments), _HookNotice(system_message, "ok")
    return _continue(original_arguments), None


_MAX_HOOK_OUTPUT_BYTES = 1_000_000


async def _read_capped(reader: asyncio.StreamReader | None, limit: int) -> bytes:
    # Keeps the first ``limit`` bytes but drains the rest: an unbounded ``communicate()``
    # lets a flooding hook exhaust memory, while simply not reading blocks the child on a
    # full pipe, where it would never reach the timeout.
    if reader is None:
        return b""
    chunks: list[bytes] = []
    size = 0
    while chunk := await reader.read(65536):
        remaining = limit - size
        if remaining > 0:
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
    return b"".join(chunks)


async def _feed_stdin_and_wait(
    process: asyncio.subprocess.Process, stdin: bytes
) -> None:
    writer = process.stdin
    if writer is not None:
        with contextlib.suppress(ConnectionResetError, BrokenPipeError, OSError):
            writer.write(stdin)
            await writer.drain()
        with contextlib.suppress(ConnectionResetError, BrokenPipeError, OSError):
            writer.close()
    await process.wait()


async def _run_command(command: str, cwd: Path, timeout_s: float, stdin: bytes) -> str:
    """Spawn ``command`` in ``cwd`` with ``stdin``; return stdout text.

    Raises on any failure (spawn error, timeout, non-zero exit); the caller maps that to
    the point's fail action.

    The command runs through the platform shell, matching the legacy executor: a
    ``hooks.toml`` command is a shell line, and real ones use pipes, ``&&``, redirects,
    globs and ``$VAR``. Splitting it into argv instead would leave most of them exiting
    non-zero or -- worse -- exiting *zero* with the metacharacters passed through as
    literal arguments, whose non-JSON stdout a non-strict hook maps to allow-and-continue.

    ``start_new_session`` puts the command in its own process group so a timeout tears
    down the whole tree; a hook that spawns children would otherwise orphan them.
    """
    if not command.strip():
        raise _HookCommandError("hook command is empty")
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            start_new_session=True,
        )
    except OSError as error:
        raise _HookCommandError(f"failed to start: {error}") from None
    try:
        stdout_bytes, stderr_bytes, _ = await asyncio.wait_for(
            asyncio.gather(
                _read_capped(process.stdout, _MAX_HOOK_OUTPUT_BYTES),
                _read_capped(process.stderr, _MAX_HOOK_OUTPUT_BYTES),
                _feed_stdin_and_wait(process, stdin),
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        raise _HookCommandError("timed out") from None
    finally:
        # Kill the process group on timeout, cancellation, or error so a hook never
        # orphans a child. A no-op once the process has exited.
        await kill(process)
    if process.returncode != 0:
        # Prefer stderr: stdout is reserved for the JSON response and is likely
        # empty / garbage when the hook crashed.
        reason = (
            stderr_bytes.decode("utf-8", errors="replace").strip()
            or stdout_bytes.decode("utf-8", errors="replace").strip()
            or f"exited with code {process.returncode}"
        )
        raise _HookCommandError(reason)
    return stdout_bytes.decode("utf-8", errors="replace")


def build_pre_tool_call_handler(
    *,
    name: str,
    command: str,
    match: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    strict: bool = False,
) -> ToolScopedHook[RustPreToolCallHookInput, RustPreToolCallHookResult]:
    async def handler(
        hook_input: RustPreToolCallHookInput, context: HookContext
    ) -> RustPreToolCallHookResult:
        original_arguments = dict(hook_input.tool_call.call.arguments)
        # Notice lines use the leaf name (bash, not file_system.bash); the payload keeps
        # the qualified name per the harness spec.
        display_name = qualified_tool_name(hook_input.tool_call.call).rsplit(".", 1)[-1]
        try:
            payload = _stdin_payload(hook_input, context)
            stdin = json.dumps(payload).encode("utf-8")
            stdout = await _run_command(
                command, context.config.workspace.cwd, timeout_s, stdin
            )
        except Exception as error:
            # Any execution failure maps to the point's fail action, never an exception.
            result, notice = _fail(
                strict,
                original_arguments,
                tool_name=display_name,
                failure_reason=_execution_reason(error),
            )
        else:
            result, notice = _interpret_stdout(
                stdout,
                original_arguments,
                strict=strict,
                tool_name=display_name,
                call=hook_input.tool_call.call,
            )
        await _emit_completed(
            context,
            name=name,
            scope="pre_tool",
            tool_call_id=hook_input.tool_call.call_id,
            notice=notice,
        )
        return result

    return _tool_scoped(handler, match)


# --- post_tool ---------------------------------------------------------------


def _tool_result_text(result: RustToolResult) -> str:
    # Falls back to rendering ``structured_content`` as ``key: value`` lines: a tool like
    # bash carries its output only there, and a hook reading ``tool_output_text`` would
    # otherwise see nothing.
    text = "".join(
        block.text
        for block in result.content
        if isinstance(block, RustTextContentBlock)
    )
    if text:
        return text
    structured = result.structured_content
    if isinstance(structured, dict):
        return "\n".join(f"{key}: {value}" for key, value in structured.items())
    if structured is None:
        return ""
    return str(structured)


def _post_tool_payload(
    hook_input: RustPostToolCallHookInput, context: HookContext
) -> dict[str, Any]:
    tool_call = hook_input.tool_call
    result = hook_input.tool_result
    is_failure = isinstance(result, RustToolFailureResult)
    return {
        **_base_payload(context),
        "hook_event_name": _POST_TOOL_EVENT_NAME,
        "tool_name": qualified_tool_name(tool_call.call),
        "tool_call_id": tool_call.call_id,
        "tool_input": _author_tool_input(tool_call.call),
        "tool_status": "failure" if is_failure else "success",
        # A failed call's structured_content is partial/undefined; report None so a
        # post_tool hook keys off tool_error/tool_output_text, not a leaked payload.
        "tool_output": None if is_failure else result.structured_content,
        # On failure, surface the error message rather than rendering the failed call's
        # partial content/structured_content -- _tool_result_text
        # would otherwise leak a bash-style result's structured stdout, or return an
        # empty string that drops the error entirely.
        "tool_output_text": (
            result.error.message
            if isinstance(result, RustToolFailureResult)
            else _tool_result_text(result)
        ),
        "tool_error": result.error.message
        if isinstance(result, RustToolFailureResult)
        else None,
        # Stub: the harness protocol carries no per-tool timing (RustPostToolCallHookInput
        # has only tool_call + tool_result), so there is nothing to plumb here yet. A hook
        # keying off duration_ms always sees 0 until the Core surfaces execution timing.
        "duration_ms": 0,
    }


def _post_tool_fail(
    strict: bool, result: RustToolResult, *, failure_reason: str | None = None
) -> tuple[RustToolResult, _HookNotice | None]:
    # ``strict`` blanks the model-visible result so a failed guard does not leak output it
    # never got to inspect. Both fields are cleared: Core falls back to
    # ``structured_content`` when ``content`` is empty (bash puts stdout only there), so
    # clearing ``content`` alone would still leak it.
    if strict:
        return (
            result.model_copy(update={"content": [], "structured_content": None}),
            _HookNotice("Cleared tool result (strict)", "error"),
        )
    return result, _HookNotice(failure_reason or "hook failed", "warning")


def _interpret_post_tool_stdout(  # noqa: PLR0911 - one early return per stdout shape
    stdout: str, result: RustToolResult, *, strict: bool
) -> tuple[RustToolResult, _HookNotice | None]:
    """Map exit-0 stdout to the result the model sees + notice. Empty stdout is a no-op."""
    text = stdout.strip()
    if not text:
        return result, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        return _post_tool_fail(
            strict,
            result,
            failure_reason=f"stdout did not match the hook response schema: {error}",
        )
    if not isinstance(parsed, dict):
        return _post_tool_fail(
            strict,
            result,
            failure_reason="stdout did not match the hook response schema: not an object",
        )

    extra = _additional_context(parsed)
    system_message = _system_message(parsed)

    if parsed.get("decision") == "deny":
        # The tool already ran; a deny replaces the model-visible content with the reason
        # rather than un-running the call. structured_content is cleared too: programmatic
        # tool calling reads it and ignores content, so a bash-style result (stdout only in
        # structured_content) would otherwise leak the un-redacted payload to the in-sandbox
        # program despite the deny.
        reason = parsed.get("reason")
        reason_text = str(reason) if reason else _POST_TOOL_DENY_REASON
        content = _text_reason(reason_text)
        replaced_chars = len(reason_text)
        if extra:
            content = [*content, RustTextContentBlock(text=extra)]
            replaced_chars += len(extra)
        return (
            result.model_copy(update={"content": content, "structured_content": None}),
            _HookNotice(
                system_message or f"Replaced tool result ({replaced_chars} chars)",
                "warning",
            ),
        )

    if extra:
        # Core prefers non-empty content over structured_content. When a tool put its
        # output only in structured_content (e.g. bash), materialize that text into content
        # before appending the note, or the model would see the note and lose the output.
        base = list(result.content)
        if not base:
            materialized = _tool_result_text(result)
            if materialized:
                base = [RustTextContentBlock(text=materialized)]
        appended: list[RustContentBlock] = [*base, RustTextContentBlock(text=extra)]
        return (
            result.model_copy(update={"content": appended}),
            _HookNotice(
                system_message or f"Appended {len(extra)} chars to tool result",
                "warning",
            ),
        )
    if system_message:
        return result, _HookNotice(system_message, "ok")
    return result, None


def _additional_context(parsed: dict[str, Any]) -> str | None:
    hook_output = parsed.get("hook_specific_output")
    if isinstance(hook_output, dict):
        extra = hook_output.get("additional_context")
        if isinstance(extra, str) and extra:
            return extra
    return None


def build_post_tool_call_handler(
    *,
    name: str,
    command: str,
    match: str | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    strict: bool = False,
) -> ToolScopedHook[RustPostToolCallHookInput, RustPostToolCallHookResult]:
    async def handler(
        hook_input: RustPostToolCallHookInput, context: HookContext
    ) -> RustPostToolCallHookResult:
        result = hook_input.tool_result
        try:
            payload = _post_tool_payload(hook_input, context)
            stdin = json.dumps(payload).encode("utf-8")
            stdout = await _run_command(
                command, context.config.workspace.cwd, timeout_s, stdin
            )
        except Exception as error:
            # Any execution failure maps to the point's fail action, never an exception.
            new_result, notice = _post_tool_fail(
                strict, result, failure_reason=_execution_reason(error)
            )
        else:
            new_result, notice = _interpret_post_tool_stdout(
                stdout, result, strict=strict
            )
        await _emit_completed(
            context,
            name=name,
            scope="post_tool",
            tool_call_id=hook_input.tool_call.call_id,
            notice=notice,
        )
        return RustPostToolCallHookResult(
            output=RustPostToolCallOutput(tool_result=new_result)
        )

    return _tool_scoped(handler, match)


# --- post_agent --------------------------------------------------------------


def _accept() -> RustPostAgentTurnHookResult:
    return RustPostAgentTurnHookResult(
        output=RustCompletionHookAccept(acceptance=RustAcceptCandidate())
    )


def _retry(feedback: str) -> RustPostAgentTurnHookResult:
    return RustPostAgentTurnHookResult(
        output=RustCompletionHookRetry(feedback=_text_reason(feedback))
    )


def _post_agent_payload(context: HookContext) -> dict[str, Any]:
    return {**_base_payload(context), "hook_event_name": _POST_AGENT_EVENT_NAME}


def _interpret_post_agent_stdout(
    stdout: str,
) -> tuple[RustPostAgentTurnHookResult, _HookNotice | None]:
    """Map exit-0 stdout to accept/retry. Anything but an explicit deny accepts."""
    text = stdout.strip()
    if not text:
        return _accept(), None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        return (
            _accept(),
            _HookNotice(
                f"stdout did not match the hook response schema: {error}", "warning"
            ),
        )
    if not isinstance(parsed, dict):
        return (
            _accept(),
            _HookNotice(
                "stdout did not match the hook response schema: not an object",
                "warning",
            ),
        )
    system_message = _system_message(parsed)
    if parsed.get("decision") == "deny":
        reason = parsed.get("reason")
        return (
            _retry(str(reason) if reason else _POST_AGENT_DENY_REASON),
            _HookNotice(system_message or "Requested turn revision", "warning"),
        )
    if system_message:
        return _accept(), _HookNotice(system_message, "ok")
    return _accept(), None


def build_post_agent_turn_handler(
    *,
    name: str,
    command: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    strict: bool = False,
) -> PostAgentTurnHookHandler:
    async def handler(
        _hook_input: RustCompletionHookInput, context: HookContext
    ) -> RustPostAgentTurnHookResult:
        try:
            payload = _post_agent_payload(context)
            stdin = json.dumps(payload).encode("utf-8")
            stdout = await _run_command(
                command, context.config.workspace.cwd, timeout_s, stdin
            )
        except Exception as error:
            # A failed review hook always accepts the turn, but the reason still
            # reaches the notice line so a broken hook is visible.
            await _emit_completed(
                context,
                name=name,
                scope="post_agent",
                tool_call_id=None,
                notice=_HookNotice(_execution_reason(error), "warning"),
            )
            return _accept()
        result, notice = _interpret_post_agent_stdout(stdout)
        await _emit_completed(
            context, name=name, scope="post_agent", tool_call_id=None, notice=notice
        )
        return result

    return handler
