"""Validation and deterministic values for local background-process Actions."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject
from mistralai_vibe_local_harness.vibe._processes._backend import (
    ProcessCommandEnvironment,
    TerminalBackend,
    TerminalBackendError,
)
from mistralai_vibe_local_harness.vibe._processes._manager import (
    ProcessManagerError,
    ProcessStartRequest,
)
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig

type ProcessToolName = Literal[
    "process.start", "process.output", "process.write", "process.list", "process.stop"
]
type ProcessControlKey = Literal[
    "ctrl_c",
    "ctrl_d",
    "ctrl_z",
    "esc",
    "tab",
    "enter",
    "backspace",
    "up",
    "down",
    "left",
    "right",
]

_CONTROL_BYTES: dict[ProcessControlKey, bytes] = {
    "ctrl_c": b"\x03",
    "ctrl_d": b"\x04",
    "ctrl_z": b"\x1a",
    "esc": b"\x1b",
    "tab": b"\x09",
    "enter": b"\x0d",
    "backspace": b"\x7f",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "left": b"\x1b[D",
    "right": b"\x1b[C",
}
_POSIX_ENV_DEFAULTS = {
    "TERM": "xterm-256color",
    "COLUMNS": "120",
    "LINES": "40",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LESS": "-FX",
    "DEBIAN_FRONTEND": "noninteractive",
}
_POWERSHELL_ENV_DEFAULTS = {"GIT_PAGER": "more", "PAGER": "more"}
_MAX_OUTPUT_WAIT_MS = 30_000


@dataclass(frozen=True, slots=True)
class ProcessArgumentIssue:
    location: str
    type: str
    message: str


class ProcessActionError(Exception):
    def __init__(self, error: RustProtocolError) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True, slots=True)
class ValidatedProcessStart:
    process_id: str
    command: str
    cwd: Path
    explicit_env: dict[str, str]
    process_env: dict[str, str]


@dataclass(frozen=True, slots=True)
class ValidatedProcessOutput:
    process_id: str
    from_end: bool
    cursor: int
    wait_ms: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class ValidatedProcessWrite:
    process_id: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ValidatedProcessList:
    pass


@dataclass(frozen=True, slots=True)
class ValidatedProcessStop:
    process_id: str


type ValidatedProcessAction = (
    ValidatedProcessStart
    | ValidatedProcessOutput
    | ValidatedProcessWrite
    | ValidatedProcessList
    | ValidatedProcessStop
)
type MutatingProcessAction = (
    ValidatedProcessStart | ValidatedProcessWrite | ValidatedProcessStop
)


def validate_process_action(
    action: RustRuntimeBuiltinToolCallAction,
    *,
    session_id: str,
    config: LocalRuntimeAdapterConfig,
) -> ValidatedProcessAction:
    name = action.call.name
    arguments = action.call.arguments
    if name == "process.start":
        return _validate_start(action, session_id=session_id, config=config)
    if name == "process.output":
        return _validate_output(action)
    if name == "process.write":
        return _validate_write(action, windows=config.command_environment != "unix")
    if name == "process.list":
        return ValidatedProcessList()
    if name == "process.stop":
        return ValidatedProcessStop(process_id=_required_string(arguments, "processId"))
    raise ValueError(f"Not a process Action: {name}")


def validate_process_config(config: LocalRuntimeAdapterConfig) -> None:
    issues = _environment_issues(config.env, windows=_is_windows(config))
    if issues:
        raise ValueError("; ".join(issue.message for issue in issues))
    if config.process_authority == "host_shell":
        command_environment = _process_command_environment(config)
        if (os.name == "nt") != (command_environment != "unix"):
            raise ValueError("command environment does not match the operating system")


def resolve_process_start(
    request: ValidatedProcessStart,
    *,
    backend: TerminalBackend,
    configured_shell: str | None,
    created_at: str,
) -> ProcessStartRequest:
    try:
        shell = backend.resolve_shell(configured_shell, request.process_env)
    except TerminalBackendError as error:
        raise ProcessActionError(
            process_error(
                "process_start_failed",
                "Background process could not be started",
                {"processId": request.process_id, "stage": "shell_resolution"},
            )
        ) from error
    return ProcessStartRequest(
        process_id=request.process_id,
        command=request.command,
        cwd=request.cwd,
        env=request.process_env,
        shell=shell,
        created_at=created_at,
    )


def process_id(session_id: str, action_id: str, call_id: str) -> str:
    session_tag = hashlib.sha256(session_id.encode()).hexdigest()[:12]
    digest = hashlib.sha256(
        b"process\0"
        + session_id.encode()
        + b"\0"
        + action_id.encode()
        + b"\0"
        + call_id.encode()
        + b"\0"
    ).hexdigest()[:24]
    return f"process-{session_tag}-{digest}"


def process_request_sha256(request: MutatingProcessAction) -> str:
    if isinstance(request, ValidatedProcessStart):
        payload: JsonObject = {
            "operation": "start",
            "processId": request.process_id,
            "command": request.command,
            "cwd": str(request.cwd),
            "env": cast(JsonValue, request.explicit_env),
        }
    elif isinstance(request, ValidatedProcessWrite):
        payload = {
            "operation": "write",
            "processId": request.process_id,
            "inputBase64": base64.b64encode(request.data).decode("ascii"),
        }
    else:
        payload = {"operation": "stop", "processId": request.process_id}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def process_error(code: str, message: str, details: JsonObject) -> RustProtocolError:
    return RustProtocolError(
        code=code, message=message, retryable=False, details=details
    )


def manager_error(error: ProcessManagerError) -> RustProtocolError:
    return process_error(error.code, error.message, cast(JsonObject, error.details))


def process_succeeded(
    action: RustRuntimeBuiltinToolCallAction, structured_content: JsonObject
) -> RustToolSucceededEvent:
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(
            structured_content=cast(JsonValue, structured_content)
        ),
    )


def process_failed(
    action: RustRuntimeBuiltinToolCallAction, error: RustProtocolError
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(error=error),
    )


def _validate_start(
    action: RustRuntimeBuiltinToolCallAction,
    *,
    session_id: str,
    config: LocalRuntimeAdapterConfig,
) -> ValidatedProcessStart:
    arguments = action.call.arguments
    command = _required_string(arguments, "command")
    raw_env = arguments.get("env", {})
    explicit_env = (
        {key: value for key, value in raw_env.items() if isinstance(value, str)}
        if isinstance(raw_env, dict)
        else {}
    )
    issues = _environment_issues(explicit_env, windows=_is_windows(config))
    if not command.strip():
        issues.append(
            ProcessArgumentIssue(
                "command", "empty_command", "Command must not be empty"
            )
        )
    cwd, cwd_issue = _resolve_working_directory(
        arguments.get("cwd", ""), config.cwd, windows=_is_windows(config)
    )
    if cwd_issue is not None:
        issues.append(cwd_issue)
    _raise_argument_issues("process.start", issues)
    assert cwd is not None
    return ValidatedProcessStart(
        process_id=process_id(session_id, action.action_id, action.call_id),
        command=command,
        cwd=cwd,
        explicit_env=explicit_env,
        process_env=_merge_process_environment(
            config.env,
            explicit_env,
            command_environment=_process_command_environment(config),
        ),
    )


def _validate_output(
    action: RustRuntimeBuiltinToolCallAction,
) -> ValidatedProcessOutput:
    arguments = action.call.arguments
    wait_ms = _integer(arguments, "waitMs", 0)
    issues = []
    if wait_ms > _MAX_OUTPUT_WAIT_MS:
        issues.append(
            ProcessArgumentIssue(
                "waitMs",
                "value_too_large",
                f"waitMs must be at most {_MAX_OUTPUT_WAIT_MS}",
            )
        )
    _raise_argument_issues("process.output", issues)
    return ValidatedProcessOutput(
        process_id=_required_string(arguments, "processId"),
        from_end=arguments.get("from", "start") == "end",
        cursor=_integer(arguments, "cursor", 0),
        wait_ms=wait_ms,
        max_bytes=min(_integer(arguments, "maxBytes", 16_000), 64_000),
    )


def _validate_write(
    action: RustRuntimeBuiltinToolCallAction, *, windows: bool
) -> ValidatedProcessWrite:
    arguments = action.call.arguments
    process_identifier = _required_string(arguments, "processId")
    issues: list[ProcessArgumentIssue] = []
    data = b""
    if "text" in arguments:
        text = _required_string(arguments, "text", allow_empty=True)
        if not text:
            issues.append(
                ProcessArgumentIssue(
                    "text", "empty_input", "Process input must not be empty"
                )
            )
        else:
            data = text.encode()
    elif "control" in arguments:
        controls = arguments["control"]
        if not isinstance(controls, list):
            raise ValueError("process.write control must be a list")
        data = b"".join(
            _CONTROL_BYTES[cast(ProcessControlKey, key)] for key in controls
        )
    else:
        encoded = _required_string(arguments, "bytesBase64")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            issues.append(
                ProcessArgumentIssue(
                    "bytesBase64",
                    "invalid_base64",
                    "bytesBase64 must contain valid base64",
                )
            )
        if windows and data:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                issues.append(
                    ProcessArgumentIssue(
                        "bytesBase64",
                        "invalid_utf8",
                        "bytesBase64 must contain UTF-8 on Windows",
                    )
                )
    _raise_argument_issues("process.write", issues)
    return ValidatedProcessWrite(process_id=process_identifier, data=data)


def _environment_issues(
    env: dict[str, str], *, windows: bool
) -> list[ProcessArgumentIssue]:
    issues: list[ProcessArgumentIssue] = []
    folded_names: set[str] = set()
    duplicate = False
    for name, value in env.items():
        if not name:
            issues.append(
                ProcessArgumentIssue(
                    "env",
                    "invalid_environment_name",
                    "Environment variable names must not be empty",
                )
            )
        if "=" in name:
            issues.append(
                ProcessArgumentIssue(
                    "env",
                    "invalid_environment_name",
                    "Environment variable names must not contain '='",
                )
            )
        if "\0" in name:
            issues.append(
                ProcessArgumentIssue(
                    "env",
                    "invalid_environment_name",
                    "Environment variable names must not contain a null character",
                )
            )
        if "\0" in value:
            issues.append(
                ProcessArgumentIssue(
                    f"env.{name}",
                    "invalid_environment_value",
                    "Environment variable values must not contain a null character",
                )
            )
        if windows:
            folded = name.casefold()
            duplicate = duplicate or folded in folded_names
            folded_names.add(folded)
    if duplicate:
        issues.append(
            ProcessArgumentIssue(
                "env",
                "duplicate_environment_name",
                "Environment variable names must be unique ignoring case on Windows",
            )
        )
    return issues


def _resolve_working_directory(
    value: JsonValue, session_cwd: Path, *, windows: bool
) -> tuple[Path | None, ProcessArgumentIssue | None]:
    if not isinstance(value, str):
        raise ValueError("process.start cwd must be a string")
    raw = value.strip()
    if not raw:
        raw = str(session_cwd)
    if windows:
        raw = _normalize_windows_drive_path(raw)
        if _invalid_windows_path(raw):
            return None, _cwd_issue("Working directory is invalid")
    try:
        candidate = Path(raw).expanduser()
        resolved = (
            (session_cwd / candidate).resolve()
            if not candidate.is_absolute()
            else candidate.resolve()
        )
    except (OSError, RuntimeError, ValueError):
        return None, _cwd_issue("Working directory is invalid")
    if not resolved.is_dir():
        return None, _cwd_issue("Working directory must exist and be a directory")
    return resolved, None


def _merge_process_environment(
    base: dict[str, str],
    explicit: dict[str, str],
    *,
    command_environment: ProcessCommandEnvironment,
) -> dict[str, str]:
    windows = command_environment != "unix"
    merged = dict(base)
    defaults = (
        _POWERSHELL_ENV_DEFAULTS
        if command_environment == "powershell"
        else _POSIX_ENV_DEFAULTS
    )
    for name, value in defaults.items():
        _setdefault_environment(merged, name, value, windows=windows)
    for name, value in explicit.items():
        if windows:
            folded = name.casefold()
            merged = {
                key: item for key, item in merged.items() if key.casefold() != folded
            }
        merged[name] = value
    return merged


def _setdefault_environment(
    env: dict[str, str], name: str, value: str, *, windows: bool
) -> None:
    if windows and any(key.casefold() == name.casefold() for key in env):
        return
    env.setdefault(name, value)


def _raise_argument_issues(
    tool: ProcessToolName, issues: list[ProcessArgumentIssue]
) -> None:
    if not issues:
        return
    sorted_issues = sorted(
        issues, key=lambda item: (item.location, item.type, item.message)
    )
    raise ProcessActionError(
        process_error(
            "invalid_arguments",
            f"Invalid arguments for {tool}",
            {
                "tool": tool,
                "errors": cast(
                    JsonValue,
                    [
                        {
                            "location": issue.location,
                            "type": issue.type,
                            "message": issue.message,
                        }
                        for issue in sorted_issues
                    ],
                ),
            },
        )
    )


def _required_string(
    arguments: JsonObject, name: str, *, allow_empty: bool = False
) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"process Action {name} must be a nonempty string")
    return value


def _integer(arguments: JsonObject, name: str, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"process Action {name} must be an integer")
    return value


def _process_command_environment(
    config: LocalRuntimeAdapterConfig,
) -> ProcessCommandEnvironment:
    if config.command_environment not in {"unix", "git_bash", "powershell"}:
        raise ValueError(
            "background processes require a host-shell command environment"
        )
    return cast(ProcessCommandEnvironment, config.command_environment)


def _is_windows(config: LocalRuntimeAdapterConfig) -> bool:
    return config.command_environment in {"git_bash", "powershell"}


def _normalize_windows_drive_path(path: str) -> str:
    match = re.fullmatch(r"/([A-Za-z])(?:[/\\](.*))?", path)
    if match is None:
        return path
    suffix = match.group(2)
    return f"{match.group(1)}:/{suffix or ''}"


def _invalid_windows_path(path: str) -> bool:
    return bool(
        re.match(r"^[A-Za-z]:(?![/\\])", path)
        or (path.startswith(("/", "\\")) and not path.startswith(("//", "\\\\")))
    )


def _cwd_issue(message: str) -> ProcessArgumentIssue:
    return ProcessArgumentIssue("cwd", "invalid_working_directory", message)


__all__ = [
    "MutatingProcessAction",
    "ProcessActionError",
    "ProcessArgumentIssue",
    "ValidatedProcessAction",
    "ValidatedProcessList",
    "ValidatedProcessOutput",
    "ValidatedProcessStart",
    "ValidatedProcessStop",
    "ValidatedProcessWrite",
    "manager_error",
    "process_error",
    "process_failed",
    "process_id",
    "process_request_sha256",
    "process_succeeded",
    "resolve_process_start",
    "validate_process_action",
    "validate_process_config",
]
