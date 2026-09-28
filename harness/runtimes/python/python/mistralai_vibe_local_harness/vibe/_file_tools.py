from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import stat
import tempfile
from typing import IO, Annotated, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
)

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig

SNIFF_BYTES = 4_096
_FIRST_PRINTABLE = 0x20
_DEL = 0x7F
_C1_CONTROL_END = 0x9F
DEFAULT_LINE_LIMIT = 2_000
MAX_READ_BYTES = 50 * 1_024
MAX_WRITE_BYTES = 64_000
MAX_EDIT_FILE_SIZE_BYTES = 512 * 1_024 * 1_024
SEARCH_REPLACE_ANNOTATION_KEY = "mistralai.vibe.sdk.search_replace"


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
    was_truncated: bool = False


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


class WriteFileResult(BaseModel):
    path: str
    bytes_written: int
    file_existed: bool


class SearchReplaceBlock(BaseModel):
    old_str: str = Field(min_length=1)
    new_str: str
    replace_all: bool = False


class SearchReplaceArgs(BaseModel):
    file_path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    content: list[SearchReplaceBlock] = Field(min_length=1)


class SearchReplaceResult(BaseModel):
    file: str
    lines_changed: int
    warnings: list[str] = Field(default_factory=list)


class SearchReplacePreviewBlock(BaseModel):
    old_start_line: int
    new_start_line: int
    old_lines: list[str]
    new_lines: list[str]


class SearchReplaceAnnotations(BaseModel):
    blocks: list[SearchReplacePreviewBlock]


async def execute_file_tool(
    action: RustRuntimeBuiltinToolCallAction,
    config: LocalRuntimeAdapterConfig,
    *,
    additional_read_roots: tuple[Path, ...] = (),
    authorized_path: Path | None = None,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    return await asyncio.to_thread(
        _execute_file_tool_sync,
        action,
        config,
        additional_read_roots=additional_read_roots,
        authorized_path=authorized_path,
    )


def _execute_file_tool_sync(
    action: RustRuntimeBuiltinToolCallAction,
    config: LocalRuntimeAdapterConfig,
    *,
    additional_read_roots: tuple[Path, ...] = (),
    authorized_path: Path | None = None,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    try:
        match action.call.name:
            case "file_system.read_file":
                read_args = ReadFileArgs.model_validate(action.call.arguments)
                result = read_file(
                    read_args,
                    _target_path(
                        read_args.path,
                        config,
                        authorized_path,
                        additional_roots=additional_read_roots,
                    ),
                )
                return _succeeded(action, result.model_dump(mode="json"))
            case "file_system.write_file":
                write_args = WriteFileArgs.model_validate(action.call.arguments)
                result = write_file(
                    write_args, _target_path(write_args.path, config, authorized_path)
                )
                return _succeeded(action, result.model_dump(mode="json"))
            case "file_system.search_replace":
                edit_args = SearchReplaceArgs.model_validate(action.call.arguments)
                result, annotations = search_replace(
                    edit_args,
                    _target_path(edit_args.file_path, config, authorized_path),
                )
                return _succeeded(
                    action,
                    result.model_dump(mode="json"),
                    meta={
                        SEARCH_REPLACE_ANNOTATION_KEY: annotations.model_dump(
                            mode="json"
                        )
                    },
                )
            case _:
                raise ValueError(f"Unsupported file tool: {action.call.name}")
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        return _failed(action, str(exc))


def read_file(args: ReadFileArgs, path: Path) -> ReadFileResult:
    handle, status = _open_file(path)
    with handle:
        file_size_bytes = status.st_size
        raw_prefix = handle.read(SNIFF_BYTES)
    for encoding in _candidate_encodings(raw_prefix):
        try:
            offset = _resolve_read_offset(path, encoding=encoding, offset=args.offset)
            content, was_truncated = _read_content(
                path, encoding=encoding, offset=offset, limit=args.limit
            )
            return ReadFileResult(
                path=str(path),
                content=content,
                file_size_bytes=file_size_bytes,
                returned_bytes=len(content.encode("utf-8")),
                offset=offset,
                lines_read=len(content.splitlines()),
                was_truncated=was_truncated,
            )
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode text file with supported encodings: {path}")


def write_file(args: WriteFileArgs, path: Path) -> WriteFileResult:
    content_bytes = args.content.encode("utf-8")
    if len(content_bytes) > MAX_WRITE_BYTES:
        raise ValueError(f"Content exceeds {MAX_WRITE_BYTES} bytes limit")
    if path.exists() and path.is_dir():
        raise ValueError(f"Path is a directory, not a file: {path}")

    file_existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, args.content, "utf-8")
    return WriteFileResult(
        path=str(path), bytes_written=len(content_bytes), file_existed=file_existed
    )


def search_replace(
    args: SearchReplaceArgs, path: Path
) -> tuple[SearchReplaceResult, SearchReplaceAnnotations]:
    handle, status = _open_file(path)
    with handle:
        if status.st_size > MAX_EDIT_FILE_SIZE_BYTES:
            raise ValueError(
                f"File exceeds {MAX_EDIT_FILE_SIZE_BYTES} byte edit limit: {path}"
            )
        raw = handle.read()
    original, encoding = _decode_editable_text(raw, path)
    updated = original
    previews: list[SearchReplacePreviewBlock] = []
    lines_changed = 0
    warnings: list[str] = []

    for index, block in enumerate(args.content):
        if block.old_str == block.new_str:
            raise ValueError(f"block {index}: old_str and new_str must differ")
        matches = updated.count(block.old_str)
        if matches == 0:
            raise ValueError(f"block {index}: old_str not found in {path}")
        if matches > 1 and not block.replace_all:
            raise ValueError(
                f"block {index}: old_str is not unique; found {matches} matches in {path}"
            )

        replacement_count = matches if block.replace_all else 1
        old_lines = block.old_str.splitlines(keepends=True)
        new_lines = block.new_str.splitlines(keepends=True)
        line_delta = 0
        for match_start in _find_matches(
            updated, block.old_str, limit=replacement_count
        ):
            old_start_line = updated[:match_start].count("\n") + 1
            previews.append(
                SearchReplacePreviewBlock(
                    old_start_line=old_start_line,
                    new_start_line=old_start_line + line_delta,
                    old_lines=old_lines,
                    new_lines=new_lines,
                )
            )
            line_delta += len(new_lines) - len(old_lines)
        lines_changed += max(len(old_lines), len(new_lines)) * replacement_count
        updated = updated.replace(block.old_str, block.new_str, replacement_count)

    if updated == original:
        warnings.append("search/replace blocks leave the file unchanged")
    else:
        _atomic_write_text(path, updated, encoding)

    return (
        SearchReplaceResult(
            file=str(path), lines_changed=lines_changed, warnings=warnings
        ),
        SearchReplaceAnnotations(blocks=previews),
    )


def _target_path(
    raw_path: str,
    config: LocalRuntimeAdapterConfig,
    authorized: Path | None,
    *,
    additional_roots: tuple[Path, ...] = (),
) -> Path:
    """The path to act on: the one the Host cleared, else a fresh resolution.

    Re-resolving a cleared path would discard a grant made past the roots, and
    would let a symlink moved since the check take effect.
    """
    if authorized is not None:
        return authorized
    return _resolve_file_path(raw_path, config, additional_roots=additional_roots)


def _roots_are_a_boundary(config: LocalRuntimeAdapterConfig) -> bool:
    """False when a command reads the path anyway and no one is left to ask.

    Host-shell commands carry the Host process's permissions (harness ADR 0009)
    and bypassed approvals retire the resolver, so the refusal would only pick
    which tool the model uses.
    """
    return not (config.process_authority == "host_shell" and config.bypass_approval)


def _resolve_file_path(
    raw_path: str,
    config: LocalRuntimeAdapterConfig,
    *,
    additional_roots: tuple[Path, ...] = (),
) -> Path:
    if not raw_path.strip():
        raise ValueError("Path cannot be empty")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = config.cwd / path
    resolved = path.resolve()
    if not _roots_are_a_boundary(config):
        return resolved
    roots = (*(config.workspace_roots or (config.cwd,)), *additional_roots)
    resolved_roots = tuple(root.expanduser().resolve() for root in roots)
    if not any(resolved.is_relative_to(root) for root in resolved_roots):
        raise ValueError(f"Path is outside the workspace: {resolved}")
    return resolved


def _find_matches(text: str, needle: str, *, limit: int) -> list[int]:
    starts: list[int] = []
    start = 0
    while len(starts) < limit:
        index = text.find(needle, start)
        if index < 0:
            return starts
        starts.append(index)
        start = index + len(needle)
    return starts


_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _no_follow_opener(path: str, flags: int) -> int:
    return os.open(path, flags | _NO_FOLLOW)


def _open_bytes(path: Path) -> IO[bytes]:
    """Open for binary reading, refusing a symlinked final component.

    Every path reaching these tools is already resolved, so a link standing
    there is a swap made since. Guards that component only, and is a no-op on
    Windows, which has no ``O_NOFOLLOW``.
    """
    return open(path, "rb", opener=_no_follow_opener)


def _open_file(path: Path) -> tuple[IO[bytes], os.stat_result]:
    """Open a file for binary reading and return (handle, status).

    Raises ValueError for missing files, directories, and non-regular files.
    """
    try:
        handle = _open_bytes(path)
    except FileNotFoundError as exc:
        raise ValueError(f"File not found at: {path}") from exc
    status = os.fstat(handle.fileno())
    if stat.S_ISDIR(status.st_mode):
        handle.close()
        raise ValueError(f"Path is a directory, not a file: {path}")
    if not stat.S_ISREG(status.st_mode):
        handle.close()
        raise ValueError(f"Path is not a regular file: {path}")
    return handle, status


def _open_text(path: Path, encoding: str) -> IO[str]:
    """Text counterpart of :func:`_open_bytes`."""
    return open(
        path, encoding=encoding, errors="strict", newline="", opener=_no_follow_opener
    )


def _resolve_read_offset(path: Path, *, encoding: str, offset: int) -> int:
    if offset >= 0:
        return offset
    if offset != -1:
        raise ValueError(
            "offset must be greater than or equal to 0, or -1 to read the last line"
        )

    line_count = 0
    with _open_text(path, encoding) as handle:
        for line_count, _line in enumerate(handle, start=1):  # noqa: B007 - counting only
            pass
    return max(line_count - 1, 0)


def _read_content(
    path: Path, *, encoding: str, offset: int, limit: int | None
) -> tuple[str, bool]:
    parts: list[str] = []
    bytes_written = 0
    seen = 0
    yielded = 0

    with _open_text(path, encoding) as handle:
        for line in handle:
            if seen < offset:
                seen += 1
                continue
            if limit is not None and yielded >= limit:
                return "".join(parts), True

            line_bytes = len(line.encode("utf-8"))
            if bytes_written + line_bytes <= MAX_READ_BYTES:
                parts.append(line)
                bytes_written += line_bytes
                yielded += 1
                continue

            remaining = MAX_READ_BYTES - bytes_written
            if remaining > 0:
                parts.append(
                    line.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
                )
            return "".join(parts), True

    return "".join(parts), False


def _decode_editable_text(raw: bytes, path: Path) -> tuple[str, str]:
    for encoding in _candidate_encodings(raw[:SNIFF_BYTES]):
        try:
            text = raw.decode(encoding, errors="strict")
            if _looks_binary(text, raw, encoding):
                raise ValueError(f"Binary files are not supported: {path}")
            return text, encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode text file with supported encodings: {path}")


def _looks_binary(text: str, raw: bytes, encoding: str) -> bool:
    if b"\x00" in raw and not encoding.startswith(("utf-16", "utf-32")):
        return True
    return any(
        character not in "\t\n\r\v\f\x1c\x1d\x1e\x85"
        and (
            ord(character) < _FIRST_PRINTABLE
            or _DEL <= ord(character) <= _C1_CONTROL_END
        )
        for character in text[:SNIFF_BYTES]
    )


def _atomic_write_text(path: Path, content: str, encoding: str) -> None:
    temporary_path: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            errors="strict",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        if mode is not None:
            temporary_path.chmod(mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _candidate_encodings(raw: bytes) -> list[str]:
    candidates = [_encoding_from_bom(raw), "utf-8", "cp1252", "latin-1"]
    return list(dict.fromkeys(encoding for encoding in candidates if encoding))


def _encoding_from_bom(raw: bytes) -> str | None:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe\x00\x00") or raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    return None


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
