"""Build app-server events for Vibe client scenarios."""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any
import uuid

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.scenario import AppServerEvent

_fixture = json.loads(FIXTURE_PATH.read_text())
SESSION_ID: str = _fixture["sessionId"]
TURN_ID: str = _fixture["turnId"]
QUEUE_ITEM_ID: str = _fixture["handshake"]["session/turn/enqueue"]["queueItemId"]
_TIMESTAMP = 1_787_593_260_000


def _event(method: str, params: dict[str, Any]) -> AppServerEvent:
    return {"method": method, "params": params}


def _entry(
    entry_type: str,
    *,
    entry_id: str | None = None,
    generation_status: str = "completed",
    **fields: Any,
) -> dict[str, Any]:
    return {
        "id": entry_id or str(uuid.uuid4()),
        "sessionId": SESSION_ID,
        "turnId": TURN_ID,
        "createdAt": _TIMESTAMP,
        "updatedAt": _TIMESTAMP,
        "generationStatus": generation_status,
        "relatedEntryId": None,
        "type": entry_type,
        **fields,
    }


def _added(entry: dict[str, Any]) -> AppServerEvent:
    return _event(
        "history/entryAdded",
        {
            "sessionId": SESSION_ID,
            "emittedAt": _TIMESTAMP,
            "turnId": TURN_ID,
            "entry": entry,
        },
    )


def _updated(entry_id: str, patch: list[dict[str, Any]]) -> AppServerEvent:
    return _event(
        "history/entryUpdated",
        {
            "sessionId": SESSION_ID,
            "emittedAt": _TIMESTAMP,
            "turnId": TURN_ID,
            "entryId": entry_id,
            "patch": patch,
        },
    )


def _turn(*, completed: bool, stop_reason: str | None = None) -> AppServerEvent:
    return _event(
        "turn/completed" if completed else "turn/started",
        {
            "sessionId": SESSION_ID,
            "emittedAt": _TIMESTAMP,
            "turn": {
                "id": TURN_ID,
                "sessionId": SESSION_ID,
                "status": "completed" if completed else "in_progress",
                "startedAt": _TIMESTAMP,
                "completedAt": _TIMESTAMP if completed else None,
                "error": None,
                "stopReason": stop_reason,
                "queueItemId": QUEUE_ITEM_ID,
            },
        },
    )


def paste(text: str) -> str:
    """Wrap clipboard text in terminal bracketed-paste markers."""
    return f"\x1b[200~{text}\x1b[201~"


def turn_started() -> AppServerEvent:
    return _turn(completed=False)


def turn_completed(stop_reason: str | None = None) -> AppServerEvent:
    """Build a `turn/completed`, optionally stopped for a reason (e.g. `limit`)."""
    return _turn(completed=True, stop_reason=stop_reason)


def turn_failed(
    code: str = "backend_error", message: str = "Upstream service error"
) -> AppServerEvent:
    """A turn/completed with status=failed and a retryable error code."""
    return _event(
        "turn/completed",
        {
            "sessionId": SESSION_ID,
            "emittedAt": _TIMESTAMP,
            "turn": {
                "id": TURN_ID,
                "sessionId": SESSION_ID,
                "status": "failed",
                "startedAt": _TIMESTAMP,
                "completedAt": _TIMESTAMP,
                "error": {"code": code, "message": message},
                "stopReason": None,
                "queueItemId": QUEUE_ITEM_ID,
            },
        },
    )


def queued_turn(enqueue_index: int, text: str = "") -> dict[str, Any]:
    """One still-queued prompt, identified by which enqueue accepted it (1-based)."""
    item_id = (
        QUEUE_ITEM_ID if enqueue_index == 1 else f"{QUEUE_ITEM_ID}-{enqueue_index}"
    )
    return {
        "id": item_id,
        "createdAt": _TIMESTAMP,
        "entries": [
            {
                "annotations": {},
                "content": [{"type": "text", "text": text}],
                "entryId": f"queued-{enqueue_index}",
                "role": "user",
            }
        ],
    }


def turn_queue_updated(
    items: list[dict[str, Any]] | None = None, *, paused: bool = False
) -> AppServerEvent:
    """Announce the prompt queue the server holds, empty by default."""
    return _event(
        "turn/queueUpdated",
        {
            "sessionId": SESSION_ID,
            "emittedAt": _TIMESTAMP,
            "queue": {"items": items or [], "paused": paused},
        },
    )


def user_msg(
    text: str,
    *,
    images: list[dict[str, Any]] | None = None,
    entry_id: str | None = None,
) -> AppServerEvent:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    content.extend(
        {"type": "image", "attachment": attachment} for attachment in images or []
    )
    return _added(
        _entry(
            "message",
            entry_id=entry_id,
            role="user",
            content=content,
            source="turn_start",
            userDisplayContent=None,
        )
    )


def file_image_attachment(alias: str, path: str | None = None) -> dict[str, Any]:
    """One file-backed image attachment; the display link targets `path`."""
    return {
        "source": {"kind": "file", "path": path if path is not None else alias},
        "alias": alias,
        "mimeType": "image/png",
    }


def inline_image_attachment(alias: str) -> dict[str, Any]:
    """One inline image attachment with no file on disk to link."""
    return {
        "source": {"kind": "inline", "data": "aW1hZ2U="},
        "alias": alias,
        "mimeType": "image/png",
    }


def user_msg_with_images(
    text: str, attachments: Sequence[dict[str, Any]]
) -> AppServerEvent:
    """One user message whose content also carries image attachments."""
    return _added(
        _entry(
            "message",
            role="user",
            content=[
                {"type": "text", "text": text},
                *(
                    {"type": "image", "attachment": attachment}
                    for attachment in attachments
                ),
            ],
            source="turn_start",
            userDisplayContent=None,
        )
    )


def assistant_msg(
    text: str, *, entry_id: str | None = None, generation_status: str = "completed"
) -> AppServerEvent:
    return _added(
        _entry(
            "message",
            entry_id=entry_id,
            generation_status=generation_status,
            role="assistant",
            content=[{"type": "text", "text": text}],
            source=None,
            userDisplayContent=None,
        )
    )


def assistant_delta(entry_id: str, text: str) -> AppServerEvent:
    """Append one streaming assistant delta without completing its entry."""
    return _updated(
        entry_id, [{"op": "append", "path": "/content/0/text", "value": text}]
    )


def complete_entry(entry_id: str) -> AppServerEvent:
    """Mark one streaming entry completed."""
    return _updated(
        entry_id, [{"op": "replace", "path": "/generationStatus", "value": "completed"}]
    )


def notice(message: str, detail: dict[str, Any]) -> AppServerEvent:
    """Build a completed notice entry; `detail` carries its discriminating kind."""
    return _added(_entry("notice", level="info", message=message, detail=detail))


def session_title_updated(title: str) -> AppServerEvent:
    return notice(
        "Session title updated", {"kind": "session_title_updated", "title": title}
    )


def scheduled_loop_fired(message: str, loop_id: str) -> AppServerEvent:
    return notice(message, {"kind": "scheduled_loop_fired", "loopId": loop_id})


def hook_run_started(scope: str, tool_call_id: str) -> AppServerEvent:
    """Open the hook-run container that later hook lines render inside."""
    return notice(
        "Hook run started",
        {"kind": "hook_run_started", "scope": scope, "toolCallId": tool_call_id},
    )


def hook_completed(
    hook_name: str, content: str, status: str, scope: str, tool_call_id: str
) -> AppServerEvent:
    """A completed hook whose output renders as a system message line."""
    return notice(
        "Hook completed",
        {
            "kind": "hook_completed",
            "scope": scope,
            "toolCallId": tool_call_id,
            "hookName": hook_name,
            "status": status,
            "content": content,
        },
    )


def server_warning(message: str) -> AppServerEvent:
    """Build a server-pushed operational warning (Python `ServerWarning`)."""
    return _event("warning", {"warning": {"code": "warning", "message": message}})


def reasoning(text: str, *, generation_status: str = "completed") -> AppServerEvent:
    return _added(
        _entry("reasoning", generation_status=generation_status, text=text, summary=[])
    )


def _bash_detail(command: str, suffix: str = "") -> dict[str, Any]:
    return {
        "toolName": "bash",
        "display": {
            "summary": f"bash: {command}",
            "content": None,
            "suffix": suffix,
            "verb": "Running",
            "message": command,
            "settledVerb": "Ran",
            "settledMessage": command,
            "statusText": "Running command",
        },
        "kind": "shell",
        "input": {"command": command},
    }


def bash(
    command: str,
    stdout: str = "",
    *,
    entry_id: str | None = None,
    suffix: str = "",
    warnings: Sequence[str] = (),
) -> AppServerEvent:
    """Build a completed bash effect, optionally under a stable entry id."""
    return _added(
        _entry(
            "effect",
            entry_id=entry_id,
            title="bash",
            detail=_bash_detail(command, suffix),
            state={
                "status": "completed",
                "outputText": stdout,
                "output": {
                    "stdout": stdout,
                    "stderr": "",
                    "output": stdout,
                    "truncated": False,
                },
                "display": {
                    "success": True,
                    "verb": "Ran",
                    "message": command,
                    "suffix": suffix,
                    "warnings": list(warnings),
                },
            },
        )
    )


def _manual_shell_detail(command: str) -> dict[str, Any]:
    """Detail for a user's `!` command: a standalone `shell` tool, not an agent bash call."""
    return {
        "toolName": "shell",
        "display": {
            "summary": f"shell: {command}",
            "content": None,
            "suffix": "",
            "verb": "Running",
            "message": command,
            "settledVerb": "Ran",
            "settledMessage": command,
            "statusText": "Running command",
        },
        "kind": "shell",
        "input": {"command": command},
    }


def manual_shell(
    command: str, stdout: str = "", *, entry_id: str | None = None
) -> AppServerEvent:
    """Build a completed manual `!` shell effect: turnless and never folded."""
    event = _added(
        _entry(
            "effect",
            entry_id=entry_id,
            title="shell",
            detail=_manual_shell_detail(command),
            state={
                "status": "completed",
                "outputText": stdout,
                "output": {
                    "stdout": stdout,
                    "stderr": "",
                    "output": stdout,
                    "truncated": False,
                },
                "display": {"success": True, "verb": "", "message": f"Ran {command}"},
            },
        )
    )
    event["params"]["turnId"] = None
    event["params"]["entry"]["turnId"] = None
    return event


def bash_failed(
    command: str, error: str = "command failed", *, entry_id: str | None = None
) -> AppServerEvent:
    """Build a failed bash effect whose result display reports success=False."""
    return _added(
        _entry(
            "effect",
            entry_id=entry_id,
            title="bash",
            detail=_bash_detail(command),
            state={
                "status": "failed",
                "error": {"message": error, "code": None, "details": None},
                "output": None,
                "outputText": "",
                "durationMs": 0.0,
                "display": {
                    "success": False,
                    "verb": "Failed",
                    "message": command,
                    "suffix": "",
                    "warnings": [],
                },
            },
        )
    )


def bash_started(command: str, entry_id: str) -> AppServerEvent:
    """Build a running bash effect whose output stream can be fed."""
    return _added(
        _entry(
            "effect",
            entry_id=entry_id,
            generation_status="in_progress",
            title="bash",
            detail=_bash_detail(command),
            state={"status": "running", "outputText": ""},
        )
    )


def bash_output_chunk(entry_id: str, chunk: str) -> AppServerEvent:
    """Append one streamed output chunk to a running bash effect."""
    return _updated(
        entry_id, [{"op": "append", "path": "/state/outputText", "value": chunk}]
    )


def bash_completed(
    entry_id: str,
    command: str,
    stdout: str = "",
    *,
    suffix: str = "",
    warnings: Sequence[str] = (),
) -> AppServerEvent:
    """Settle a running bash effect with its final output and display."""
    return _updated(
        entry_id,
        [
            {
                "op": "replace",
                "path": "/state",
                "value": {
                    "status": "completed",
                    "output": {
                        "stdout": stdout,
                        "stderr": "",
                        "output": "",
                        "truncated": False,
                    },
                    "display": {
                        "success": True,
                        "verb": "Ran",
                        "message": command,
                        "suffix": suffix,
                        "warnings": list(warnings),
                    },
                },
            },
            {"op": "replace", "path": "/generationStatus", "value": "completed"},
            {"op": "replace", "path": "/updatedAt", "value": _TIMESTAMP},
        ],
    )


def read_file(
    path: str,
    content: str,
    num_lines: int,
    *,
    entry_id: str | None = None,
    warnings: Sequence[str] = (),
) -> AppServerEvent:
    """Build a completed read-file effect."""
    return _added(
        _entry(
            "effect",
            entry_id=entry_id,
            title="read_file",
            detail={
                "toolName": "read_file",
                "display": {
                    "summary": f"Reading {path}",
                    "content": None,
                    "suffix": "",
                    "verb": "Reading",
                    "message": path,
                    "settledVerb": "Read",
                    "settledMessage": path,
                    "statusText": "Reading file",
                },
                "kind": "file_read",
                "input": {"filePath": path, "offset": None, "limit": 2000},
            },
            state={
                "status": "completed",
                "output": {
                    "filePath": path,
                    "content": content,
                    "numLines": num_lines,
                    "startLine": 1,
                    "requestedOffset": None,
                    "requestedLimit": 2000,
                    "totalLines": num_lines,
                    "wasTruncated": False,
                },
                "display": {
                    "success": True,
                    "verb": "Read",
                    "message": f"{num_lines} {'line' if num_lines == 1 else 'lines'} from {path}",
                    "warnings": list(warnings),
                },
            },
        )
    )


def subagent(
    task: str, *, tool_name: str = "subagent.spawn", response: str | None = None
) -> AppServerEvent:
    """Build a completed subagent effect with optional native task output."""
    return _added(
        _entry(
            "effect",
            title=tool_name,
            detail={
                "toolName": tool_name,
                "display": {
                    "summary": f"Exploring: {task}",
                    "content": None,
                    "suffix": "",
                    "verb": "Exploring",
                    "message": task,
                    "settledVerb": "Explored",
                    "settledMessage": task,
                    "statusText": "Running subagent",
                },
                "kind": "subagent",
                "input": {"task": task, "agent": "explore"},
                "childSessionId": "child-session",
            },
            state={
                "status": "completed",
                "output": (
                    {"response": response, "turns_used": 1, "completed": True}
                    if response is not None
                    else None
                ),
                "display": {"success": True, "verb": "Explored", "message": task},
            },
        )
    )


_SUBAGENT_WAIT_ID = "effect-subagent-wait"


def subagent_wait_started() -> AppServerEvent:
    """Build the running wait effect that precedes a completion notification."""
    return _added(
        _entry(
            "effect",
            entry_id=_SUBAGENT_WAIT_ID,
            generation_status="in_progress",
            title="subagent.wait",
            detail={
                "toolName": "subagent.wait",
                "display": {
                    "summary": "Waiting for a subagent",
                    "content": None,
                    "suffix": "",
                    "verb": "Waiting",
                    "message": "for a subagent",
                    "settledVerb": "Waited",
                    "settledMessage": "for a subagent",
                    "statusText": "Waiting for a subagent",
                },
                "kind": "tool",
                "input": {"agentName": "explore-1", "timeoutMs": 10_000},
            },
            state={"status": "running", "outputText": ""},
        )
    )


def subagent_wait_completed(response: str) -> AppServerEvent:
    """Complete the wait after the Harness has injected its notification."""
    return _updated(
        _SUBAGENT_WAIT_ID,
        [
            {
                "op": "replace",
                "path": "/state",
                "value": {
                    "status": "completed",
                    "output": {
                        "type": "success",
                        "content": [],
                        "structured_content": {"type": "success", "value": response},
                    },
                    "outputText": "",
                    "display": {
                        "success": True,
                        "verb": "Waited",
                        "message": "for a subagent",
                    },
                },
            },
            {"op": "replace", "path": "/generationStatus", "value": "completed"},
        ],
    )


def subagent_notification(response: str) -> AppServerEvent:
    """Build the parent-session message carrying a completed subagent response."""
    notification = {
        "id": "subagent:child-session:explore-1:1:completed",
        "source": {
            "type": "subagent",
            "agent_name": "explore-1",
            "status": "completed",
        },
        "level": "info",
        "message": "Subagent explore-1 completed turn 1.",
    }
    return _added(
        _entry(
            "message",
            role="user",
            content=[
                {
                    "type": "text",
                    "text": f"Runtime notification:\n{json.dumps(notification)}",
                },
                {"type": "text", "text": response},
            ],
            source="harness",
            userDisplayContent=None,
        )
    )


def loaded_skill(
    name: str, content: str, *, related_entry_id: str | None = None
) -> AppServerEvent:
    """Build a completed skill effect."""
    return _added(
        _entry(
            "effect",
            title="skill",
            relatedEntryId=related_entry_id,
            detail={
                "toolName": "skill",
                "display": {
                    "summary": f"Loading skill: {name}",
                    "content": None,
                    "suffix": "",
                    "verb": "Loading",
                    "message": f"skill: {name}",
                    "settledVerb": "Loaded",
                    "settledMessage": f"skill: {name}",
                    "statusText": "Loading skill",
                },
                "kind": "skill",
                "input": {"name": name},
            },
            state={
                "status": "completed",
                "output": None,
                "outputText": content,
                "durationMs": 0,
                "display": {
                    "success": True,
                    "verb": "Loaded",
                    "message": f"skill: {name}",
                },
            },
        )
    )


def edit_file(
    path: str,
    occurrences: list[tuple[int | None, str, str]],
    *,
    replace_all: bool = False,
) -> AppServerEvent:
    """Build a completed file-edit effect from `(start_line, old_text, new_text)`."""
    old_string = occurrences[0][1] if occurrences else ""
    new_string = occurrences[0][2] if occurrences else ""
    return _added(
        _entry(
            "effect",
            title="edit",
            detail={
                "toolName": "edit",
                "display": {
                    "summary": f"Editing {path}",
                    "content": None,
                    "suffix": "",
                    "verb": "Editing",
                    "message": path,
                    "settledVerb": "Edited",
                    "settledMessage": path,
                    "statusText": "Editing files",
                },
                "kind": "file_edit",
                "input": {
                    "filePath": path,
                    "oldString": old_string,
                    "newString": new_string,
                    "replaceAll": replace_all,
                },
            },
            state={
                "status": "completed",
                "output": {
                    "file": path,
                    "oldString": old_string,
                    "newString": new_string,
                    "occurrences": [
                        {
                            "startLine": start_line,
                            "oldText": old_text,
                            "newText": new_text,
                        }
                        for start_line, old_text, new_text in occurrences
                    ],
                },
                "display": {"success": True, "verb": "Edited", "message": path},
            },
        )
    )


def write_file(path: str, content: str) -> AppServerEvent:
    """Build a completed file-write effect whose body is the written content."""
    return _added(
        _entry(
            "effect",
            title="write",
            detail={
                "toolName": "write",
                "display": {
                    "summary": f"Writing {path}",
                    "content": None,
                    "suffix": "",
                    "verb": "Writing",
                    "message": path,
                    "settledVerb": "Wrote",
                    "settledMessage": path,
                    "statusText": "Writing files",
                },
                "kind": "file_write",
                "input": {"filePath": path, "content": content},
            },
            state={
                "status": "completed",
                "output": {"filePath": path, "content": content},
                "display": {"success": True, "verb": "Wrote", "message": path},
            },
        )
    )


def todo(todos: Sequence[dict[str, Any]]) -> AppServerEvent:
    """Build a completed todo effect carrying `todos` items in mixed states."""
    return _added(
        _entry(
            "effect",
            title="todo",
            detail={
                "toolName": "todo",
                "display": {
                    "summary": "Updating todos",
                    "content": None,
                    "suffix": "",
                    "verb": "Updating",
                    "message": "todos",
                    "settledVerb": "Updated",
                    "settledMessage": "todos",
                    "statusText": "Updating todos",
                },
                "kind": "todo",
                "input": {"action": "write", "todos": list(todos)},
            },
            state={
                "status": "completed",
                "output": {"todos": list(todos)},
                "display": {"success": True, "verb": "Updated", "message": "todos"},
            },
        )
    )


def todo_item(item_id: str, content: str, status: str) -> dict[str, Any]:
    """One todo item; `status` is pending/in_progress/completed/cancelled."""
    return {"id": item_id, "content": content, "status": status, "priority": "medium"}


def _web_fetch_detail(url: str, suffix: str) -> dict[str, Any]:
    """The web-fetch call detail shared by its event builders."""
    return {
        "toolName": "web_fetch",
        "display": {
            "summary": f"Fetching {url}",
            "content": None,
            "suffix": suffix,
            "verb": "Fetching",
            "message": url,
            "settledVerb": "Fetched",
            "settledMessage": url,
            "statusText": "Fetching url",
        },
        "kind": "web_fetch",
        "input": {"url": url, "timeout": None},
    }


def web_fetch(url: str, content: str, *, was_truncated: bool = False) -> AppServerEvent:
    """Build a completed web-fetch effect whose settled header linkifies `url`."""
    return _added(
        _entry(
            "effect",
            title="web_fetch",
            detail=_web_fetch_detail(url, "(truncated)" if was_truncated else ""),
            state={
                "status": "completed",
                "output": {
                    "url": url,
                    "content": content,
                    "contentType": "text/html",
                    "wasTruncated": was_truncated,
                },
                "display": {
                    "success": True,
                    "verb": "Fetched",
                    "message": url,
                    "suffix": "(truncated)" if was_truncated else "",
                },
            },
        )
    )


def web_fetch_without_output(url: str) -> AppServerEvent:
    """Build a completed web-fetch effect whose state carries no output."""
    return _added(
        _entry(
            "effect",
            title="web_fetch",
            detail=_web_fetch_detail(url, ""),
            state={
                "status": "completed",
                "display": {"success": True, "verb": "Fetched", "message": url},
            },
        )
    )


def _callback_entry(
    questions: list[dict[str, Any]], footer_note: str | None, callback_id: str
) -> dict[str, Any]:
    return _entry(
        "callback",
        entry_id=callback_id,
        generation_status="in_progress",
        callbackId=callback_id,
        title="User input required",
        detail={
            "kind": "user_input",
            "request": {"questions": questions, "footerNote": footer_note},
            "relatedEntryId": None,
        },
        state={"status": "open"},
    )


def ask_user_question_added(
    questions: list[dict[str, Any]],
    *,
    footer_note: str | None = None,
    callback_id: str = "callback-ask_user_question",
) -> AppServerEvent:
    """Build the callback history entry the projector broadcasts alongside the call."""
    return _added(_callback_entry(questions, footer_note, callback_id))


def ask_user_question_answered(
    answers: list[dict[str, Any]], *, callback_id: str = "callback-ask_user_question"
) -> AppServerEvent:
    """Move the callback history entry to `answered`, as the projector does on reply."""
    return _updated(
        callback_id,
        [
            {
                "op": "replace",
                "path": "/state",
                "value": {
                    "status": "answered",
                    "output": {
                        "type": "user_input",
                        "result": {"answers": answers, "cancelled": False},
                    },
                },
            },
            {"op": "replace", "path": "/generationStatus", "value": "completed"},
        ],
    )


def ask_user_question(
    questions: list[dict[str, Any]],
    *,
    footer_note: str | None = None,
    callback_id: str = "callback-ask_user_question",
    request_id: int = 9001,
) -> AppServerEvent:
    """Build the server-to-client `callback/call` an `ask_user_question` tool opens."""
    entry = _callback_entry(questions, footer_note, callback_id)
    event = _event("callback/call", {"callback": entry})
    event["id"] = request_id
    return event


def _tool_approval_entry(
    tool_name: str,
    effect_kind: str,
    tool_input: dict[str, Any],
    required_permissions: list[dict[str, Any]],
    callback_id: str,
    session_id: str,
) -> dict[str, Any]:
    return _entry(
        "callback",
        entry_id=callback_id,
        generation_status="in_progress",
        sessionId=session_id,
        callbackId=callback_id,
        title=f"Allow {tool_name}?",
        detail={
            "kind": "approval",
            "effect": {
                "toolName": tool_name,
                "display": {
                    "summary": tool_name,
                    "content": None,
                    "suffix": "",
                    "verb": "Running",
                    "message": tool_name,
                    "settledVerb": "Ran",
                    "settledMessage": tool_name,
                    "statusText": f"Waiting for approval to run {tool_name}",
                },
                "kind": effect_kind,
                "input": tool_input,
            },
            "requiredPermissions": required_permissions,
            "choices": [
                "approve",
                "approve_for_session",
                "approve_permanently",
                "deny",
                "cancel_turn",
            ],
            "relatedEntryId": None,
        },
        state={"status": "open"},
    )


def tool_approval_added(
    tool_name: str,
    effect_kind: str,
    tool_input: dict[str, Any],
    *,
    required_permissions: list[dict[str, Any]] | None = None,
    callback_id: str = "callback-tool-approval",
    session_id: str = SESSION_ID,
) -> AppServerEvent:
    """Build the callback history entry broadcast with a tool approval request."""
    return _added(
        _tool_approval_entry(
            tool_name,
            effect_kind,
            tool_input,
            required_permissions or [],
            callback_id,
            session_id,
        )
    )


def tool_approval(
    tool_name: str,
    effect_kind: str,
    tool_input: dict[str, Any],
    *,
    required_permissions: list[dict[str, Any]] | None = None,
    callback_id: str = "callback-tool-approval",
    request_id: int = 9001,
    session_id: str = SESSION_ID,
) -> AppServerEvent:
    """Build the server-to-client `callback/call` for a tool approval."""
    entry = _tool_approval_entry(
        tool_name,
        effect_kind,
        tool_input,
        required_permissions or [],
        callback_id,
        session_id,
    )
    event = _event("callback/call", {"callback": entry})
    event["id"] = request_id
    return event


def ask_user_question_effect(
    question: str, answer: str, *, is_other: bool = False
) -> AppServerEvent:
    """Build the settled `ask_user_question` effect the tool call leaves behind."""
    return _added(
        _entry(
            "effect",
            title="ask_user_question",
            detail={
                "toolName": "ask_user_question",
                "display": {
                    "summary": f"Asking: {question}",
                    "content": None,
                    "suffix": "",
                    "verb": "Asking",
                    "message": question,
                    "settledVerb": "Asked",
                    "settledMessage": question,
                    "statusText": "Waiting for user input",
                },
                "kind": "user_question",
                "input": {
                    "questions": [
                        {
                            "question": question,
                            "options": [{"label": answer}, {"label": "Something else"}],
                        }
                    ],
                    "footerNote": None,
                },
            },
            state={
                "status": "completed",
                "output": {
                    "answers": [
                        {"question": question, "answer": answer, "isOther": is_other}
                    ],
                    "cancelled": False,
                },
                "display": {
                    "success": True,
                    "verb": "Answered",
                    "message": f'"{question}" \u2192 {"(Other) " if is_other else ""}{answer}',
                },
            },
        )
    )


def _web_search_id(query: str) -> str:
    return f"effect-web_search-{query}"


def _web_search_detail(query: str) -> dict[str, Any]:
    return {
        "toolName": "web_search",
        "display": {
            "summary": f"Searching the web: '{query}'",
            "content": None,
            "suffix": "",
            "verb": "Searching",
            "message": f"the web: '{query}'",
            "settledVerb": "Searched",
            "settledMessage": f"the web: '{query}'",
            "statusText": "Searching the web",
        },
        "kind": "web_search",
        "input": {"query": query},
    }


def web_search_started(query: str) -> AppServerEvent:
    return _added(
        _entry(
            "effect",
            entry_id=_web_search_id(query),
            generation_status="in_progress",
            title="web_search",
            detail=_web_search_detail(query),
            state={"status": "running", "outputText": ""},
        )
    )


def web_search_completed(
    query: str, answer: str, *, sources: list[dict[str, str]] | None = None
) -> AppServerEvent:
    return _updated(
        _web_search_id(query),
        [
            {
                "op": "replace",
                "path": "/state",
                "value": {
                    "status": "completed",
                    "output": {
                        "query": query,
                        "answer": answer,
                        "sources": sources or [],
                    },
                    "display": {
                        "success": True,
                        "verb": "Searched",
                        "message": f"the web: '{query}'",
                    },
                },
            },
            {"op": "replace", "path": "/generationStatus", "value": "completed"},
            {"op": "replace", "path": "/updatedAt", "value": _TIMESTAMP},
        ],
    )
