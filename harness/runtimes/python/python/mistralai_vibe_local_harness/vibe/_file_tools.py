from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe import _sandbox_helper
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._sandbox import (
    SandboxAdapter,
    run_helper,
    sandbox_path,
)
from mistralai_vibe_local_harness.vibe._sandbox_helper import FileToolName

DEFAULT_LINE_LIMIT = _sandbox_helper.DEFAULT_LINE_LIMIT
MAX_READ_BYTES = _sandbox_helper.MAX_READ_BYTES
MAX_WRITE_BYTES = _sandbox_helper.MAX_WRITE_BYTES
MAX_WRITE_PREVIOUS_CONTENT_BYTES = _sandbox_helper.MAX_WRITE_PREVIOUS_CONTENT_BYTES
MAX_EDIT_FILE_SIZE_BYTES = _sandbox_helper.MAX_EDIT_FILE_SIZE_BYTES
SEARCH_REPLACE_ANNOTATION_KEY = "mistralai.vibe.sdk.search_replace"
WRITE_FILE_ANNOTATION_KEY = "mistralai.vibe.sdk.write_file"
SANDBOX_FILE_TOOL_TIMEOUT_SECONDS = 120.0


class ReadFileArgs(BaseModel):
    path: str
    offset: int = 0
    limit: int | None = Field(default=DEFAULT_LINE_LIMIT, ge=1)


class ReadFileResult(BaseModel):
    path: str
    content: str
    file_size_bytes: int
    returned_bytes: int
    offset: int = 0
    lines_read: int
    total_lines: int | None = None
    next_offset: int | None = None
    was_truncated: bool = False


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


class WriteFileResult(BaseModel):
    path: str
    bytes_written: int
    file_existed: bool


class WriteFileAnnotations(BaseModel):
    """What the write replaced.

    An annotation rather than a result: the model wrote the new content, it does
    not need the old one read back to it.
    """

    previous_content: str


class EditArgs(BaseModel):
    file_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    old_string: str = Field(min_length=1)
    new_string: str
    replace_all: bool = False


class EditResult(BaseModel):
    file: str
    lines_changed: int
    warnings: list[str] = Field(default_factory=list)


class EditPreviewBlock(BaseModel):
    old_start_line: int
    new_start_line: int
    old_lines: list[str]
    new_lines: list[str]


class EditAnnotations(BaseModel):
    """Applied replacement previews and the text the edit replaced.

    ``previous_content`` is the file's whole pre-edit text, carried so readers
    that need the before-state (diffing, restore) can take it exactly instead
    of reversing the previews. It is None when the file did not decode as
    UTF-8 or was larger than the write annotation cap.
    """

    blocks: list[EditPreviewBlock]
    previous_content: str | None = None


class ReadFileResponse(BaseModel):
    result: ReadFileResult
    annotations: None = None

    def meta(self) -> dict[str, JsonValue] | None:
        return None


class WriteFileResponse(BaseModel):
    result: WriteFileResult
    annotations: str | None = None

    def meta(self) -> dict[str, JsonValue] | None:
        if self.annotations is None:
            return None
        previous = WriteFileAnnotations(previous_content=self.annotations)
        return {WRITE_FILE_ANNOTATION_KEY: previous.model_dump(mode="json")}


class EditResponse(BaseModel):
    result: EditResult
    annotations: list[EditPreviewBlock]
    previous_content: str | None = None

    def meta(self) -> dict[str, JsonValue] | None:
        previews = EditAnnotations(
            blocks=self.annotations, previous_content=self.previous_content
        )
        return {SEARCH_REPLACE_ANNOTATION_KEY: previews.model_dump(mode="json")}


@dataclass(frozen=True, slots=True)
class FileTool:
    """How one file tool's arguments and helper response are read."""

    name: FileToolName
    arguments: type[ReadFileArgs] | type[WriteFileArgs] | type[EditArgs]
    response: type[ReadFileResponse] | type[WriteFileResponse] | type[EditResponse]


FILE_TOOLS: Mapping[str, FileTool] = {
    "file_system.read_file": FileTool("read_file", ReadFileArgs, ReadFileResponse),
    "file_system.write_file": FileTool("write_file", WriteFileArgs, WriteFileResponse),
    "file_system.edit": FileTool("edit", EditArgs, EditResponse),
}


async def execute_file_tool(
    action: RustRuntimeBuiltinToolCallAction,
    config: LocalRuntimeAdapterConfig,
    *,
    additional_read_roots: tuple[Path, ...] = (),
    workspace_read_roots: tuple[Path, ...] = (),
    authorized_path: Path | None = None,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    """Run one file tool where the workspace lives.

    With a Sandbox Adapter, the tool runs in the sandbox, except reads of the
    host-owned ``additional_read_roots``, such as attachments.
    ``workspace_read_roots`` are directories where the tools run that
    ``read_file`` may read as well, such as the one saved outputs are read
    back from.
    """
    try:
        tool = _file_tool(action)
        arguments = tool.arguments.model_validate(action.call.arguments).model_dump(
            mode="json"
        )
        raw_response = await _run_file_tool(
            tool,
            arguments,
            config,
            additional_read_roots=additional_read_roots,
            workspace_read_roots=workspace_read_roots,
            authorized_path=authorized_path,
        )
        response = tool.response.model_validate(raw_response)
    except (OSError, UnicodeError, ValueError) as exc:
        return _failed(action, str(exc))
    return _succeeded(
        action, response.result.model_dump(mode="json"), meta=response.meta()
    )


def _file_tool(action: RustRuntimeBuiltinToolCallAction) -> FileTool:
    tool = FILE_TOOLS.get(action.call.name)
    if tool is None:
        raise ValueError(f"Unsupported file tool: {action.call.name}")
    return tool


async def _run_file_tool(
    tool: FileTool,
    arguments: dict[str, JsonValue],
    config: LocalRuntimeAdapterConfig,
    *,
    additional_read_roots: tuple[Path, ...],
    workspace_read_roots: tuple[Path, ...],
    authorized_path: Path | None,
) -> JsonValue:
    sandbox = config.sandbox
    raw_path = arguments[_sandbox_helper.FILE_TOOL_PATH_KEYS[tool.name]]
    on_host = sandbox is None or (
        tool.name == "read_file"
        and _is_within(
            authorized_path or sandbox_path(str(raw_path), cwd=config.workspace.cwd),
            additional_read_roots,
        )
    )
    request = {
        "tool": tool.name,
        "arguments": arguments,
        "policy": _path_policy(
            config,
            authorized_path,
            # The Host's resolver approves a sandbox path as written, unable to
            # see the sandbox's links, so the helper follows them itself.
            authorized_as_written=sandbox is not None and not on_host,
            additional_roots=_read_roots(
                tool,
                sandbox=sandbox,
                on_host=on_host,
                host=additional_read_roots,
                workspace=workspace_read_roots,
            ),
        ),
    }
    # The provider is asked for parallel tool calls, so two calls can target
    # one file in the same turn. Both read the pre-edit text before either
    # writes, and the atomic rename makes the loser's edit vanish without an
    # error, so writes to one path are serialised. Reads share the lock too:
    # one that interleaves a write returns either the whole before-state or
    # the whole after-state, never a torn one.
    async with _file_tool_lock(raw_path, config, authorized_path):
        if on_host or sandbox is None:
            return await asyncio.to_thread(_sandbox_helper.run_file_tool, request)
        return await run_helper(
            sandbox,
            "file",
            request,
            timeout=SANDBOX_FILE_TOOL_TIMEOUT_SECONDS,
            crashes=config.helper_crashes,
        )


_FILE_TOOL_LOCKS: dict[str, asyncio.Lock] = {}
_FILE_TOOL_LOCK_LOOP: asyncio.AbstractEventLoop | None = None


def _file_tool_lock(
    raw_path: JsonValue, config: LocalRuntimeAdapterConfig, authorized: Path | None
) -> asyncio.Lock:
    # Locks belong to one event loop; a stale lock from another loop is never
    # taken, so the table is rebuilt when the loop changes.
    global _FILE_TOOL_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _FILE_TOOL_LOCK_LOOP is not loop:
        _FILE_TOOL_LOCKS.clear()
        _FILE_TOOL_LOCK_LOOP = loop
    if authorized is not None:
        key = str(authorized)
    elif config.sandbox is None:
        target = Path(str(raw_path)).expanduser()
        if not target.is_absolute():
            target = Path(config.workspace.cwd) / target
        key = str(target.resolve())
    else:
        # This host's file system may not hold the path, or may link it
        # elsewhere, so the key is the sandbox-normalized path as written,
        # the same normalization the sandbox applies. A ``~`` path stays
        # unexpanded because the sandbox's home is unknown here, so ``~/a``
        # and its expanded spelling still take different locks.
        key = str(sandbox_path(str(raw_path), cwd=config.workspace.cwd))
    lock = _FILE_TOOL_LOCKS.get(key)
    if lock is None:
        lock = _FILE_TOOL_LOCKS[key] = asyncio.Lock()
    return lock


def _read_roots(
    tool: FileTool,
    *,
    sandbox: SandboxAdapter | None,
    on_host: bool,
    host: tuple[Path, ...],
    workspace: tuple[Path, ...],
) -> tuple[Path, ...]:
    if tool.name != "read_file":
        return ()
    if sandbox is None:
        return (*workspace, *host)
    # Host and sandbox roots are paths in different file systems.
    return host if on_host else workspace


def _path_policy(
    config: LocalRuntimeAdapterConfig,
    authorized: Path | None,
    *,
    authorized_as_written: bool,
    additional_roots: tuple[Path, ...],
) -> dict[str, JsonValue]:
    return {
        "cwd": str(config.workspace.cwd),
        "roots": [str(root) for root in (*config.workspace.roots, *additional_roots)],
        "roots_are_a_boundary": _roots_are_a_boundary(config),
        "authorized": None if authorized is None else str(authorized),
        "authorized_as_written": authorized_as_written,
    }


def _roots_are_a_boundary(config: LocalRuntimeAdapterConfig) -> bool:
    """False when a command reads the path anyway and no one is left to ask.

    Host-shell commands carry the Host process's permissions (harness ADR 0009),
    sandboxed commands reach the whole sandbox, and bypassed approvals retire
    the resolver, so the refusal would only pick which tool the model uses.
    """
    commands_read_anyway = (
        config.process_authority == "host_shell" or config.sandbox is not None
    )
    return not (commands_read_anyway and config.bypass_approval)


def _is_within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _succeeded(
    action: RustRuntimeBuiltinToolCallAction,
    structured_content: dict[str, JsonValue],
    *,
    meta: dict[str, JsonValue] | None = None,
) -> RustToolSucceededEvent:
    result: dict[str, JsonValue] = {
        "structured_content": cast(JsonValue, structured_content)
    }
    if meta is not None:
        result["_meta"] = cast(JsonValue, meta)
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult.model_validate(result),
    )


def _failed(
    action: RustRuntimeBuiltinToolCallAction, message: str
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code="tool_failed", message=message, retryable=False, details=None
            )
        ),
    )


__all__ = ["execute_file_tool"]
