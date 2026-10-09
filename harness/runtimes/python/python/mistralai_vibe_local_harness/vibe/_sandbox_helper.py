"""Workspace file and shell operations shared by every place a tool can run.

The local adapter calls these functions in its own process. A Sandbox Adapter
runs this file as a script under the sandbox's interpreter, so the file must
stay self-contained: standard library only, no imports from this package, and
syntax that Python 3.9 still accepts.

As a script it takes ``<operation> --request-base64 <payload>``, where the
payload is zlib-compressed JSON encoded as URL-safe base64, or
``<operation> --request-file <path>`` for a payload too large for one argument,
streamed into a file the helper deletes once read. It prints one JSON object:
``{"ok": true, "result": ...}`` or ``{"ok": false, "error": ...}``.

The ``process`` operation talks to a per-Session process server, which this
file also runs, detached, as ``process-server --session-id <id>``. The server
owns a Session's background processes so they outlive the helper call that
started them; later calls find it through a private state directory.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Iterator, Mapping, Sequence
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import IO, Any, Literal
import zlib

SNIFF_BYTES = 4_096
_FIRST_PRINTABLE = 0x20
_DEL = 0x7F
_C1_CONTROL_END = 0x9F
DEFAULT_LINE_LIMIT = 2_000
MAX_READ_BYTES = 50 * 1_024
MAX_WRITE_BYTES = 64_000
MAX_WRITE_PREVIOUS_CONTENT_BYTES = 64_000
MAX_EDIT_FILE_SIZE_BYTES = 512 * 1_024 * 1_024
MAX_OUTPUT_BYTES = 16_000
SESSION_DIRECTORY_PREFIX = "mistralai-vibe-session-"
TOOL_RESULTS_DIRECTORY = "tool-results"
_POSIX_SHELL_NAMES = ("zsh", "bash", "sh")
_POSIX_SHELL_PATHS = (
    "/bin/zsh",
    "/usr/bin/zsh",
    "/bin/bash",
    "/usr/bin/bash",
    "/bin/sh",
    "/usr/bin/sh",
)


FileToolName = Literal["read_file", "write_file", "search_replace"]
"""The file tools the helper runs."""

FILE_TOOL_PATH_KEYS: dict[FileToolName, str] = {
    "read_file": "path",
    "write_file": "path",
    "search_replace": "file_path",
}
"""The argument naming each file tool's target path."""


class ShellNotFoundError(LookupError):
    """No usable shell: the configured one is not executable, or none exists."""


# Paths


def resolve_path(
    raw_path: str, *, cwd: str, roots: Sequence[str], roots_are_a_boundary: bool
) -> Path:
    """Resolve a model-supplied path against ``cwd`` and confine it to ``roots``.

    ``roots_are_a_boundary`` is False when the caller already lets commands
    read past the roots, so refusing here would only pick which tool is used.
    """
    if not raw_path.strip():
        raise ValueError("Path cannot be empty")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    resolved = path.resolve()
    if not roots_are_a_boundary:
        return resolved
    resolved_roots = tuple(Path(root).expanduser().resolve() for root in roots)
    if not any(resolved.is_relative_to(root) for root in resolved_roots):
        raise ValueError(f"Path is outside the workspace: {resolved}")
    return resolved


# File tools


def read_file(path: Path, *, offset: int = 0, limit: int | None) -> dict[str, Any]:
    """Read a text file window, trying each supported encoding in turn."""
    handle, status = _open_file(path)
    with handle:
        file_size_bytes = status.st_size
        raw_prefix = handle.read(SNIFF_BYTES)
    for encoding in _candidate_encodings(raw_prefix):
        try:
            resolved_offset = _resolve_read_offset(
                path, encoding=encoding, offset=offset
            )
            content, was_truncated = _read_content(
                path, encoding=encoding, offset=resolved_offset, limit=limit
            )
        except UnicodeDecodeError:
            continue
        return {
            "path": str(path),
            "content": content,
            "file_size_bytes": file_size_bytes,
            "returned_bytes": len(content.encode("utf-8")),
            "offset": resolved_offset,
            "lines_read": len(content.splitlines()),
            "was_truncated": was_truncated,
        }
    raise ValueError(f"Could not decode text file with supported encodings: {path}")


def write_file(path: Path, content: str) -> tuple[dict[str, Any], str | None]:
    """Write ``content`` atomically; also return the text it replaced, if small."""
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_WRITE_BYTES:
        raise ValueError(f"Content exceeds {MAX_WRITE_BYTES} bytes limit")
    if path.exists() and path.is_dir():
        raise ValueError(f"Path is a directory, not a file: {path}")

    file_existed = path.exists()
    previous_content = _replaced_content(path) if file_existed else None
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, content, "utf-8")
    result = {
        "path": str(path),
        "bytes_written": len(content_bytes),
        "file_existed": file_existed,
    }
    return result, previous_content


def search_replace(
    path: Path, blocks: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply search/replace blocks in order; return the result and line previews.

    Each block maps ``old_str``, ``new_str`` and ``replace_all``.
    """
    handle, status = _open_file(path)
    with handle:
        if status.st_size > MAX_EDIT_FILE_SIZE_BYTES:
            raise ValueError(
                f"File exceeds {MAX_EDIT_FILE_SIZE_BYTES} byte edit limit: {path}"
            )
        raw = handle.read()
    original, encoding = _decode_editable_text(raw, path)
    updated = original
    previews: list[dict[str, Any]] = []
    lines_changed = 0
    warnings: list[str] = []

    for index, block in enumerate(blocks):
        updated, changed = _apply_block(updated, index, block, path, previews)
        lines_changed += changed

    if updated == original:
        warnings.append("search/replace blocks leave the file unchanged")
    else:
        _atomic_write_text(path, updated, encoding)

    result = {"file": str(path), "lines_changed": lines_changed, "warnings": warnings}
    return result, previews


def _apply_block(
    text: str,
    index: int,
    block: Mapping[str, Any],
    path: Path,
    previews: list[dict[str, Any]],
) -> tuple[str, int]:
    old_str: str = block["old_str"]
    new_str: str = block["new_str"]
    replace_all = bool(block.get("replace_all", False))
    if old_str == new_str:
        raise ValueError(f"block {index}: old_str and new_str must differ")
    matches = text.count(old_str)
    if matches == 0:
        raise ValueError(f"block {index}: old_str not found in {path}")
    if matches > 1 and not replace_all:
        raise ValueError(
            f"block {index}: old_str is not unique; found {matches} matches in {path}"
        )

    replacement_count = matches if replace_all else 1
    old_lines = old_str.splitlines(keepends=True)
    new_lines = new_str.splitlines(keepends=True)
    line_delta = 0
    for match_start in _find_matches(text, old_str, limit=replacement_count):
        old_start_line = text[:match_start].count("\n") + 1
        previews.append({
            "old_start_line": old_start_line,
            "new_start_line": old_start_line + line_delta,
            "old_lines": old_lines,
            "new_lines": new_lines,
        })
        line_delta += len(new_lines) - len(old_lines)
    lines_changed = max(len(old_lines), len(new_lines)) * replacement_count
    return text.replace(old_str, new_str, replacement_count), lines_changed


def run_file_tool(request: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the target path, then run one file tool.

    ``request`` carries ``tool`` (``read_file``, ``write_file`` or
    ``search_replace``), its validated ``arguments`` and the path ``policy``.
    Returns ``{"result": ..., "annotations": ...}``.
    """
    tool = request["tool"]
    arguments = request["arguments"]
    policy = request["policy"]
    if tool not in FILE_TOOL_PATH_KEYS:
        raise ValueError(f"Unsupported file tool: {tool}")
    raw_path = arguments[FILE_TOOL_PATH_KEYS[tool]]
    authorized = policy.get("authorized")
    if authorized is None:
        path = resolve_path(
            raw_path,
            cwd=policy["cwd"],
            roots=policy["roots"],
            roots_are_a_boundary=policy["roots_are_a_boundary"],
        )
    else:
        path = Path(authorized).expanduser()
    if authorized is not None and policy.get("authorized_as_written"):
        # Approved as written, by a resolver that cannot see this file
        # system's links, such as the Host's for a sandbox. Follow them here,
        # as that resolver does on its own file system; the roots are not
        # checked, since the approval may be for a path outside them. A path
        # the resolver already resolved is used as given, so a link planted
        # there since is refused when opened.
        path = path.resolve()
    if tool == "read_file":
        result = read_file(path, offset=arguments["offset"], limit=arguments["limit"])
        return {"result": result, "annotations": None}
    if tool == "write_file":
        result, previous_content = write_file(path, arguments["content"])
        return {"result": result, "annotations": previous_content}
    result, previews = search_replace(path, arguments["content"])
    return {"result": result, "annotations": previews}


def _replaced_content(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_WRITE_PREVIOUS_CONTENT_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


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
        except UnicodeDecodeError:
            continue
        if _looks_binary(text, raw, encoding):
            raise ValueError(f"Binary files are not supported: {path}")
        return text, encoding
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
            with contextlib.suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _candidate_encodings(raw: bytes) -> list[str]:
    candidates = [_encoding_from_bom(raw), "utf-8", "cp1252", "latin-1"]
    return list(dict.fromkeys(encoding for encoding in candidates if encoding))


def _encoding_from_bom(raw: bytes) -> str | None:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return None


# Saved tool results


def save_tool_result(request: Mapping[str, Any]) -> dict[str, Any]:
    """Save an offloaded tool result where the session's commands can read it.

    The file goes into an owner-only directory for the session under the
    temporary directory. Other users may share that directory, so a session
    directory found there as a symlink, owned by someone else or open to others
    is refused rather than written through.
    """
    session_id = _path_segment(request["session_id"])
    name = _path_segment(request["name"])
    content = request["content"]
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    directory = os.path.join(
        tempfile.gettempdir(), SESSION_DIRECTORY_PREFIX + session_id
    )
    results = os.path.join(directory, TOOL_RESULTS_DIRECTORY)
    _make_private_directory(directory)
    _make_private_directory(results)
    path = Path(results) / name
    _atomic_write_text(path, content, "utf-8")
    return {"path": str(path)}


def _path_segment(value: object) -> str:
    if (
        not isinstance(value, str)
        or value in {"", ".", ".."}
        or any(character in value for character in "/\\\0")
    ):
        raise ValueError(f"Not a single path segment: {value!r}")
    return value


def _make_private_directory(path: str) -> None:
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    _check_private_directory(path)


def _check_private_directory(path: str) -> None:
    status = os.lstat(path)
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError(f"Refusing a directory other users can reach: {path}")
    # Windows reports neither POSIX permission bits nor an owner uid; there the
    # temporary directory is already private to the user.
    if sys.platform == "win32":
        return
    owner = getattr(os, "geteuid", None)
    if stat.S_IMODE(status.st_mode) & 0o077 or (
        owner is not None and status.st_uid != owner()
    ):
        raise ValueError(f"Refusing a directory other users can reach: {path}")


# Skill copies


SKILLS_DIRECTORY = "mistralai-vibe-skills"
_SKILL_ENTRY_FILE = "SKILL.md"


def scan_skills(request: Mapping[str, Any]) -> dict[str, Any]:
    """Report the skill copies already here, and the open roots' own skills.

    ``request`` carries the ``digests`` of the wanted copies, the open
    ``roots``, and the caller's skill rules: the ``project_dirs`` of a root
    that hold its skills, the ``markdown_limit`` and ``metadata_limit`` in
    bytes, and the ``sample`` of files the skill tool lists. Returns the
    copies' ``root``, the digests ``present`` there intact, and one
    ``project`` item per skill found in a root.
    """
    root = os.path.join(tempfile.gettempdir(), SKILLS_DIRECTORY)
    present: list[str] = []
    # An unusable directory holds no copy to reuse; installing reports why.
    with contextlib.suppress(OSError, ValueError):
        if os.path.lexists(root):
            _check_private_directory(root)
            present = [
                digest for digest in request["digests"] if _is_intact(root, digest)
            ]
    project = [
        _read_project_skill(directory, request)
        for directory in _project_skill_directories(
            request["roots"], request["project_dirs"]
        )
    ]
    return {"root": root, "present": present, "project": project}


def install_skills(request: Mapping[str, Any]) -> dict[str, Any]:
    """Write each skill copy that is missing or broken; return the failures.

    ``request["skills"]`` maps each copy's digest to its ``[path, content]``
    files, contents in URL-safe base64. A copy is written into a staging
    directory, checked against its digest and renamed into place, inside a
    directory private to the user. An intact copy is kept, a broken one
    replaced, and one that another run moved in first is reused.
    """
    root = os.path.join(tempfile.gettempdir(), SKILLS_DIRECTORY)
    _make_private_directory(root)
    failures: dict[str, str] = {}
    for digest, files in request["skills"].items():
        try:
            _install_skill(root, _path_segment(digest), files)
        except (OSError, ValueError) as error:
            failures[digest] = str(error)
    return {"root": root, "failures": failures}


def _install_skill(root: str, digest: str, files: Sequence[Sequence[str]]) -> None:
    target = os.path.join(root, digest)
    if os.path.lexists(target):
        if _skill_digest(target) == digest:
            return
        stale = tempfile.mkdtemp(prefix=".stale-", dir=root)
        os.rename(target, os.path.join(stale, "skill"))
        shutil.rmtree(stale)
    staging = tempfile.mkdtemp(prefix=".staging-", dir=root)
    try:
        for relative, content in files:
            parts = [_path_segment(part) for part in relative.split("/")]
            path = os.path.join(staging, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "xb") as handle:
                handle.write(base64.urlsafe_b64decode(content.encode("ascii")))
        if _skill_digest(staging) != digest:
            raise ValueError("the copy does not match its digest")
        try:
            os.rename(staging, target)
        except OSError:
            if not os.path.lexists(target) or _skill_digest(target) != digest:
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _is_intact(root: str, digest: str) -> bool:
    try:
        return _skill_digest(os.path.join(root, _path_segment(digest))) == digest
    except (OSError, ValueError):
        return False


def _skill_digest(root: str) -> str | None:
    """The digest of a copy's files and paths, or None when it holds a link."""
    status = os.lstat(root)
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        return None
    found: list[tuple[str, str]] = []
    for current, directories, names in os.walk(root, followlinks=False):
        if any(os.path.islink(os.path.join(current, name)) for name in directories):
            return None
        for name in names:
            path = os.path.join(current, name)
            if not stat.S_ISREG(os.lstat(path).st_mode):
                return None
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            found.append((relative, path))
    digest = hashlib.sha256()
    for relative, path in sorted(found, key=lambda item: item[0].encode()):
        with open(path, "rb") as handle:
            content = handle.read()
        encoded = relative.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _project_skill_directories(
    roots: Sequence[str], project_dirs: Sequence[str]
) -> list[str]:
    found: list[str] = []
    for root in roots:
        for relative in project_dirs:
            base = os.path.join(root, relative)
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for name in names:
                directory = os.path.join(base, name)
                if os.path.isdir(directory) and os.path.isfile(
                    os.path.join(directory, _SKILL_ENTRY_FILE)
                ):
                    found.append(directory)
    return found


def _read_project_skill(directory: str, request: Mapping[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "directory": directory,
        "markdown": None,
        "openai": None,
        "files": [],
        "error": None,
    }
    try:
        item["markdown"] = _read_limited_text(
            os.path.join(directory, _SKILL_ENTRY_FILE), request["markdown_limit"]
        )
        metadata = os.path.join(directory, "agents", "openai.yaml")
        if os.path.isfile(metadata):
            item["openai"] = _read_limited_text(metadata, request["metadata_limit"])
        item["files"] = sample_skill_files(directory, request["sample"])
    except (OSError, ValueError) as error:
        item["error"] = str(error)
    return item


def _read_limited_text(path: str, limit: int) -> str:
    with open(path, "rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"{path} is larger than {limit} bytes")
    return content.decode("utf-8", "replace")


def sample_skill_files(directory: str, sample: Mapping[str, Any]) -> list[str]:
    """The files the skill tool lists for a skill, as it samples them.

    ``sample`` carries the ``skipped_dirs`` it never enters, the ``walk_limit``
    of files after which it stops walking, and the ``count`` it lists.
    """
    skipped = set(sample["skipped_dirs"])
    files: list[str] = []
    for current, directories, names in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if name not in skipped]
        for name in names:
            if name == _SKILL_ENTRY_FILE:
                continue
            relative = os.path.relpath(os.path.join(current, name), directory)
            files.append(relative.replace(os.sep, "/"))
        if len(files) >= sample["walk_limit"]:
            break
    return sorted(files)[: sample["count"]]


# Shell


def shell_env(
    base: Mapping[str, str], extra: Mapping[str, str], *, windows: bool
) -> dict[str, str]:
    """The environment a non-interactive bash command runs with."""
    env = {**base, "CI": "true", "NONINTERACTIVE": "1", "NO_TTY": "1", **extra}
    if windows:
        return {**env, "GIT_PAGER": "more", "PAGER": "more"}
    env.pop("LC_ALL", None)
    return {
        **env,
        "TERM": "dumb",
        "DEBIAN_FRONTEND": "noninteractive",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LESS": "-FX",
        "LC_CTYPE": "C.UTF-8",
    }


def resolve_posix_shell(configured: str | None, env: Mapping[str, str]) -> str:
    """The configured shell if executable, else the first of zsh, bash and sh."""
    search_path = env.get("PATH", "")
    if configured is not None:
        resolved = _resolve_executable(configured, search_path)
        if resolved is None:
            raise ShellNotFoundError("configured shell is not executable")
        return resolved
    for candidate in _POSIX_SHELL_NAMES:
        if found := shutil.which(candidate, path=search_path):
            return found
    for candidate in _POSIX_SHELL_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise ShellNotFoundError("no POSIX shell found")


def _resolve_executable(candidate: str, search_path: str) -> str | None:
    expanded = Path(candidate).expanduser()
    if os.sep in candidate or (os.altsep is not None and os.altsep in candidate):
        return (
            str(expanded)
            if expanded.is_file() and os.access(expanded, os.X_OK)
            else None
        )
    return shutil.which(candidate, path=search_path)


async def spawn_shell(
    argv: Sequence[str], *, cwd: str | Path, env: Mapping[str, str]
) -> asyncio.subprocess.Process:
    """Start a shell command with piped output, in its own process group."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        env=dict(env),
        start_new_session=sys.platform != "win32",
    )


async def collect_bash(
    process: asyncio.subprocess.Process,
    *,
    command: str,
    timeout_seconds: int,
    output_encoding: str = "utf-8",
) -> dict[str, Any]:
    """Wait for a spawned command and return its bounded, decoded output.

    A non-zero exit is a result, not an error: the caller reports the code.
    The process group is always killed, on success, timeout or cancellation.
    """
    task = asyncio.ensure_future(_communicate_bounded(process))
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
        if not done:
            raise TimeoutError(
                f"Command timed out after {timeout_seconds}s: {command!r}"
            )
        stdout_bytes, stdout_truncated, stderr_bytes, stderr_truncated = task.result()
        return {
            "command": command,
            "stdout": decode_output(stdout_bytes, output_encoding),
            "stderr": decode_output(stderr_bytes, output_encoding),
            "returncode": process.returncode or 0,
            "was_truncated": stdout_truncated or stderr_truncated,
        }
    finally:
        await kill(process)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def run_bash(
    command: str, *, timeout_seconds: int, cwd: str, shell: str | None
) -> dict[str, Any]:
    """Run ``command`` in a POSIX login shell with this process's environment.

    The shell runs under ``_SESSION_GUARD``, so a sandbox that kills this
    helper, as one does to a command it stops at its own time limit, kills
    the command too.
    """
    env = shell_env(os.environ, {}, windows=False)
    argv = [resolve_posix_shell(shell, env), "-lc", command]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        "-c",
        _SESSION_GUARD,
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    return await collect_bash(process, command=command, timeout_seconds=timeout_seconds)


# Leads the session a sandboxed command runs in, and kills the session's process
# group once the helper is gone. A sandbox stops a command that outlives its time
# limit by killing the helper's process group, which the command's own session is
# not in, so without it the command would run on until the sandbox is deleted.
# The guard's stdin is a pipe that only the helper holds open: end of file means
# the helper is gone. Otherwise the guard exits as the shell did.
_SESSION_GUARD = """
import os, signal, subprocess, sys, threading
signal.signal(signal.SIGINT, signal.SIG_DFL)
shell = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL)
def guard():
    try:
        while os.read(0, 4096):
            pass
    finally:
        os.killpg(0, signal.SIGKILL)
threading.Thread(target=guard, daemon=True).start()
code = shell.wait()
if code < 0:
    try:
        signal.signal(-code, signal.SIG_DFL)
    except (OSError, ValueError):
        pass
    os.kill(os.getpid(), -code)
sys.exit(code)
"""


def decode_output(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="replace")


async def kill(process: asyncio.subprocess.Process) -> None:
    """Kill the process and its group, tolerating one that already exited."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        process.terminate()
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        await process.wait()


async def _communicate_bounded(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bool, bytes, bool]:
    stdout, stderr, _ = await asyncio.gather(
        _read_bounded(process.stdout), _read_bounded(process.stderr), process.wait()
    )
    return stdout[0], stdout[1], stderr[0], stderr[1]


async def _read_bounded(reader: asyncio.StreamReader | None) -> tuple[bytes, bool]:
    if reader is None:
        return b"", False
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := await reader.read(4096):
        remaining = MAX_OUTPUT_BYTES - size
        if remaining > 0:
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks), truncated


# Background processes
#
# A process started in a sandbox must outlive the helper call that started it,
# so one server per Session owns the processes and their terminals. Each helper
# call is a client: it finds the server through files in a private state
# directory, starts one when none answers, sends one request and exits. The
# server leaves when its client is gone for good, or when nothing runs and no
# request came for a while.
#
# Another Harness runtime has a sibling server. This one is separate because
# the helper must stay one self-contained file. Its framing and stop sequence
# differ on purpose, but a fix to the token check, the stop's signals and
# grace, or the reaping of a group's processes in one likely applies to the
# other.

PROCESS_ENV_DEFAULTS = {
    "TERM": "xterm-256color",
    "COLUMNS": "120",
    "LINES": "40",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LESS": "-FX",
    "DEBIAN_FRONTEND": "noninteractive",
}
"""Variables a background process gets unless its environment sets them."""

PROCESS_SERVER_ARGUMENT = "process-server"
"""The script argument that runs the process server instead of an operation."""

_PROCESS_TOKEN_ENV = "MISTRALAI_VIBE_PROCESS_SERVER_TOKEN"
_PROCESS_PROTOCOL_VERSION = 1
_PROCESS_SERVER_HOST = "127.0.0.1"
_PROCESS_STATE_ROOT = os.path.join("mistralai-vibe-harness", "processes")
_PROCESS_FALLBACK_STATE_ROOT = "mistralai-vibe-harness-processes"
_PROCESS_FRAME_BYTES = 8 * 1_024 * 1_024
_PROCESS_STARTUP_SECONDS = 10.0
_PROCESS_CONNECT_SECONDS = 5.0
_PROCESS_RESPONSE_SECONDS = 30.0
_PROCESS_REQUEST_READ_SECONDS = 10.0
_PROCESS_STOP_GRACE_SECONDS = 2.0
_PROCESS_IDLE_SECONDS = 600.0
_PROCESS_ABANDONED_SECONDS = 3_600.0
_PROCESS_MAX_WAIT_MS = 5_000
_PROCESS_MAX_POLL_BYTES = 1_024 * 1_024
_PROCESS_MAX_GRACE_MS = 30_000
_PROCESS_TRACK_SECONDS = 0.5
_PROCESS_UNREAD_LIMIT = 16 * 1_024 * 1_024
_PROCESS_WRITE_SECONDS = 1.0
_PR_SET_CHILD_SUBREAPER = 36

# The monotonic time a process table's read began, and the table: each pid's
# parent pid, group id and whether it exited.
_ProcessSnapshot = tuple[float, dict[int, tuple[int, int, bool]]]

# Runs in the child before the shell: makes the terminal the controlling one,
# and undoes the signal state a Python parent leaves behind, then becomes the
# shell, so the shell keeps this process's pid and group.
_PROCESS_BOOTSTRAP = """
import fcntl, os, signal, sys, termios
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
if hasattr(signal, "pthread_sigmask"):
    signal.pthread_sigmask(signal.SIG_SETMASK, [])
for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
    number = getattr(signal, name, None)
    if number is not None:
        signal.signal(number, signal.SIG_DFL)
os.execv(sys.argv[1], [sys.argv[1], "-lc", sys.argv[2]])
"""


class _RefusedAtConnectError(ConnectionRefusedError):
    """Nothing listens at the recorded endpoint: the server is gone."""


def run_process_operation(request: Mapping[str, Any]) -> dict[str, Any]:
    """Send one background-process operation to the Session's process server.

    Starts the server first when none answers. Returns the server's outcome:
    ``{"outcome": "succeeded", "value": ...}`` or ``{"outcome": "failed",
    "code": ..., "message": ...}``. Raises ``OSError`` when the server cannot
    be reached or started.
    """
    if os.name == "nt":
        return _process_failure(
            "unsupported_platform", "Background processes need a POSIX sandbox"
        )
    session_id = str(request["sessionId"])
    operation = str(request["operation"])
    payload = request.get("payload") or {}
    directory = _process_state_directory(session_id)
    frame = {
        "type": "operation",
        "sessionId": session_id,
        "owner": str(request["owner"]),
        "operation": operation,
        "payload": payload,
    }
    timeout = (
        _PROCESS_RESPONSE_SECONDS
        + min(int(payload.get("waitMs", 0)), _PROCESS_MAX_WAIT_MS) / 1_000
    )
    if operation == "stop":
        # The server answers a stop once its processes are gone, which takes
        # up to two graces: one after SIGTERM, one after SIGKILL.
        timeout += (
            2 * min(int(payload.get("graceMs", 0)), _PROCESS_MAX_GRACE_MS) / 1_000
        )
    if operation == "shutdown":
        return _shut_down_process_server(directory, frame, timeout)
    for attempt in range(2):
        endpoint = _process_server_endpoint(directory, session_id)
        try:
            return _send_process_frame(endpoint, frame, timeout)
        except _RefusedAtConnectError:
            with _startup_lock(directory):
                _remove_unowned_endpoint(directory)
            if attempt:
                raise
    raise AssertionError("unreachable")


def serve_processes(argv: Sequence[str]) -> int:
    """Run the process server for the Session named in ``argv``."""
    if len(argv) != 3 or argv[1] != "--session-id":  # noqa: PLR2004
        return _usage()
    token = os.environ.pop(_PROCESS_TOKEN_ENV, "")
    if not token:
        sys.stderr.write("process server needs its token\n")
        return 2
    import fcntl

    session_id = argv[2]
    directory = _process_state_directory(session_id)
    become_subreaper()
    lock = os.open(
        os.path.join(directory, "server.lock"), os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.stderr.write("process server is already running\n")
            return 1
        server = ProcessServer(session_id, token)
        with _ProcessTCPServer(
            (_PROCESS_SERVER_HOST, 0), _ProcessRequestHandler
        ) as tcp:
            tcp.process_server = server
            server.network = tcp
            endpoint_path = os.path.join(directory, "endpoint.json")
            endpoint = {
                "host": _PROCESS_SERVER_HOST,
                "port": tcp.server_address[1],
                "token": token,
                "pid": os.getpid(),
                "protocolVersion": _PROCESS_PROTOCOL_VERSION,
            }
            _write_private_json(endpoint_path, endpoint)
            threading.Thread(target=server.watch_idle, daemon=True).start()
            try:
                tcp.serve_forever(poll_interval=0.05)
            finally:
                server.stop_all()
                _remove_own_endpoint(endpoint_path, os.getpid())
    finally:
        os.close(lock)
    return 0


class ProcessTree:
    """A process started in a new session, and the descendants that left its group.

    The process called setsid before exec, so its pid names its group, and a
    pid is not reused while a group of that id exists. A descendant can still
    leave the group with setsid or setpgid: :meth:`track` and :meth:`signal`
    find those that descend from the process, or from one found earlier, by
    walking the process table. One reparented away before any walk saw it is
    out of reach.
    """

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self._reap_lock = threading.Lock()
        self._outside_lock = threading.Lock()
        self._outside: dict[int, int] = {}
        self._tracked_read_at = float("-inf")

    def group_is_alive(self) -> bool:
        """Whether a process of the group runs: the root or what it left behind."""
        self._reap_group_orphans()
        self.process.poll()
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def is_alive(self) -> bool:
        """Whether the group, or a descendant found outside it, still runs."""
        if self.group_is_alive():
            return True
        with self._outside_lock:
            outside = dict(self._outside)
        if not outside:
            return False
        table = read_process_table()
        _reap_adopted(table, outside)
        return bool(_live_processes(table, outside))

    def track(self, snapshot: _ProcessSnapshot | None = None) -> None:
        """Record the descendants now outside the group; forget those that exited.

        Walks ``snapshot`` when given, else a process table read now. A table
        read before the last one walked is skipped: it would forget the
        descendants that one found.
        """
        read_at, table = snapshot or read_process_snapshot()
        with self._outside_lock:
            if read_at < self._tracked_read_at:
                return
            self._tracked_read_at = read_at
            _reap_adopted(table, self._outside)
            live = _live_processes(table, self._outside)
            live.update(_descendants_outside_group(table, self.process.pid, live))
            self._outside = live

    def signal(self, number: int) -> None:
        """Send ``number`` to the group and to every descendant outside it."""
        self.track()
        if self.group_is_alive():
            try:
                os.killpg(self.process.pid, number)
            except ProcessLookupError:
                pass
            except OSError:
                with contextlib.suppress(OSError):
                    os.kill(self.process.pid, number)
        with self._outside_lock:
            outside = dict(self._outside)
        own_group = os.getpgrp()
        signalled: set[int] = set()
        for pid, group in outside.items():
            if group in outside and group not in {own_group, self.process.pid}:
                # The group's leader descends from the process: so does the group.
                if group not in signalled:
                    signalled.add(group)
                    with contextlib.suppress(OSError):
                        os.killpg(group, number)
            elif pid != os.getpid():
                with contextlib.suppress(OSError):
                    os.kill(pid, number)

    def _reap_group_orphans(self) -> None:
        # As a subreaper, this process adopts the group's orphans and must reap
        # them, or their zombies would keep the group alive.
        if sys.platform != "linux" or not hasattr(os, "waitid"):
            return
        root = self.process.pid
        with self._reap_lock:
            while True:
                try:
                    child = os.waitid(
                        os.P_PGID, root, os.WEXITED | os.WNOHANG | os.WNOWAIT
                    )
                except ChildProcessError:
                    return
                if child is None:
                    return
                if child.si_pid == root:
                    if self.process.poll() is None:
                        return
                    continue
                with contextlib.suppress(ChildProcessError):
                    os.waitid(os.P_PID, child.si_pid, os.WEXITED | os.WNOHANG)


def stop_process_trees(
    trees: Sequence[ProcessTree], grace_seconds: float
) -> list[bool]:
    """Stop each tree: SIGTERM, then SIGKILL to what is left after a grace.

    Returns, for each tree, whether nothing of it was left once a second grace
    ran out.
    """
    for tree in trees:
        tree.track()
    for number in (signal.SIGTERM, signal.SIGKILL):
        live = [tree for tree in trees if tree.is_alive()]
        for tree in live:
            tree.signal(number)
        deadline = time.monotonic() + grace_seconds
        while live and time.monotonic() < deadline:
            time.sleep(0.02)
            live = [tree for tree in live if tree.is_alive()]
    return [not tree.is_alive() for tree in trees]


def become_subreaper() -> None:
    """Adopt orphaned descendants on Linux instead of leaving them to init.

    For a process that owns background processes and nothing else: a
    container's init may never reap the orphans, and their zombies would keep a
    stopped group alive. Best effort: without it, init still adopts them.
    """
    if sys.platform != "linux":
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except (AttributeError, OSError):
        return


def read_process_snapshot() -> _ProcessSnapshot:
    """A process table, and the monotonic time its read began."""
    read_at = time.monotonic()
    return read_at, read_process_table()


def read_process_table() -> dict[int, tuple[int, int, bool]]:
    """Map each visible pid to its parent pid, group id and whether it exited.

    Reads ``/proc`` where there is one, else asks ``ps``; empty when neither
    answers, so no descendant outside a group is found.
    """
    if os.path.isdir("/proc/self/task"):
        return _proc_process_table()
    return _ps_process_table()


def _proc_process_table() -> dict[int, tuple[int, int, bool]]:
    table: dict[int, tuple[int, int, bool]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join("/proc", name, "stat"), "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        # The command name may hold spaces and parentheses: fields follow the last.
        fields = raw[raw.rfind(b")") + 2 :].split()
        if len(fields) < 3:  # noqa: PLR2004
            continue
        table[int(name)] = (int(fields[1]), int(fields[2]), fields[0] in {b"Z", b"X"})
    return table


def _ps_process_table() -> dict[int, tuple[int, int, bool]]:
    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    table: dict[int, tuple[int, int, bool]] = {}
    for line in completed.stdout.decode("ascii", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 4:  # noqa: PLR2004
            continue
        with contextlib.suppress(ValueError):
            table[int(fields[0])] = (
                int(fields[1]),
                int(fields[2]),
                fields[3].startswith("Z"),
            )
    return table


def _descendants_outside_group(
    table: Mapping[int, tuple[int, int, bool]], root: int, known: Mapping[int, int]
) -> dict[int, int]:
    """The live processes under ``root``, its group or ``known`` but not in the group."""
    children: dict[int, list[int]] = {}
    for pid, (parent, _, _) in table.items():
        children.setdefault(parent, []).append(pid)
    pending = [root, *known]
    pending.extend(pid for pid, (_, group, _) in table.items() if group == root)
    seen: set[int] = set()
    found: dict[int, int] = {}
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        row = table.get(pid)
        if row is not None and row[1] != root and not row[2]:
            found[pid] = row[1]
        pending.extend(children.get(pid, ()))
    return found


def _live_processes(
    table: Mapping[int, tuple[int, int, bool]], known: Mapping[int, int]
) -> dict[int, int]:
    # A pid still in its recorded group is taken to be the same process.
    live: dict[int, int] = {}
    for pid, group in known.items():
        row = table.get(pid)
        if row is not None and row[1] == group and not row[2]:
            live[pid] = group
    return live


def _reap_adopted(
    table: Mapping[int, tuple[int, int, bool]], known: Mapping[int, int]
) -> None:
    # Reaps the exited ones this process adopted as a subreaper. The table
    # must show each as this process's exited child: its pid cannot be reused
    # before it is reaped, so no other child's exit status is taken.
    parent = os.getpid()
    for pid, group in known.items():
        if table.get(pid) == (parent, group, True):
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)


class _ProcessRecord:
    """One process the server started, and what its client has not read yet."""

    def __init__(
        self, owner: str, process: subprocess.Popen[bytes], master_fd: int
    ) -> None:
        self.owner = owner
        self.process = process
        self.tree = ProcessTree(process)
        self.master_fd = master_fd
        self.terminal_lock = threading.Lock()
        self.terminal_closed = False
        self.unread = bytearray()
        self.unread_start = 0
        self.version = 0
        self.root_exit_code: int | None = None
        self.group_alive = True
        self.ended = False
        self.discarded = False
        self.tracked_at = 0.0

    @property
    def end_cursor(self) -> int:
        return self.unread_start + len(self.unread)

    def close_terminal(self) -> None:
        # Under the lock a write holds: once closed, the descriptor's number
        # may name another process's terminal.
        with self.terminal_lock:
            if self.terminal_closed:
                return
            self.terminal_closed = True
            os.close(self.master_fd)


class ProcessServer:
    """Starts, reads, writes and stops a Session's processes for its client.

    ``unread_limit`` bounds each process's output the client has not
    acknowledged: past it the server stops reading the process's terminal, and
    the process blocks on output as on a full pipe until a poll acknowledges
    some. The server leaves once nothing runs and no request came for
    ``idle_seconds``, and stops everything and leaves when none came for
    ``abandoned_seconds``, both measured on ``clock``.
    """

    def __init__(
        self,
        session_id: str,
        token: str,
        *,
        unread_limit: int = _PROCESS_UNREAD_LIMIT,
        idle_seconds: float = _PROCESS_IDLE_SECONDS,
        abandoned_seconds: float = _PROCESS_ABANDONED_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_id = session_id
        self.token = token
        self.unread_limit = unread_limit
        self.idle_seconds = idle_seconds
        self.abandoned_seconds = abandoned_seconds
        self.clock = clock
        self.network: socketserver.BaseServer | None = None
        self.condition = threading.Condition()
        self.records: dict[str, _ProcessRecord] = {}
        self.owner: str | None = None
        # The clients a later one replaced: their late requests stop nothing.
        self.past_owners: set[str] = set()
        self.last_request = clock()
        self.closing = False
        self._snapshot_lock = threading.Lock()
        self._snapshot: _ProcessSnapshot | None = None

    def dispatch(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        """Answer one request frame from a client."""
        token = frame.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(
            token.encode("utf-8"), self.token.encode("utf-8")
        ):
            return _process_failure("authentication_failed", "Invalid server token")
        if frame.get("sessionId") != self.session_id:
            return _process_failure("foreign_session", "Server runs another Session")
        with self.condition:
            self.last_request = self.clock()
        if frame.get("type") == "handshake":
            return _process_success({"protocolVersion": _PROCESS_PROTOCOL_VERSION})
        owner = str(frame.get("owner"))
        operation = frame.get("operation")
        payload = frame.get("payload") or {}
        if operation == "shutdown":
            return self.shut_down(owner)
        handlers = {
            "start": lambda: self.start(owner, payload),
            "poll": lambda: self.poll(payload),
            "write": lambda: self.write(payload),
            "stop": lambda: self.stop(payload),
        }
        handler = handlers.get(str(operation))
        if handler is None:
            return _process_failure(
                "invalid_request", f"Unknown operation: {operation}"
            )
        self.take_over(owner)
        return handler()

    def take_over(self, owner: str) -> None:
        """Stop what an earlier client started: only one client drives a Session."""
        with self.condition:
            if self.owner == owner:
                return
            if self.owner is not None:
                self.past_owners.add(self.owner)
            self.owner = owner
            previous = [
                record for record in self.records.values() if record.owner != owner
            ]
            self.records = {
                key: record
                for key, record in self.records.items()
                if record.owner == owner
            }
            for record in previous:
                record.discarded = True
                record.unread.clear()
            self.condition.notify_all()
        stop_process_trees(
            [record.tree for record in previous], _PROCESS_STOP_GRACE_SECONDS
        )

    def start(self, owner: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        import pty

        terminal_id = str(payload["terminalId"])
        cwd = os.path.expanduser(str(payload["cwd"]))
        if not os.path.isdir(cwd):
            return _process_failure(
                "invalid_working_directory",
                "Working directory must exist and be a directory",
            )
        env = dict(os.environ)
        for name, value in PROCESS_ENV_DEFAULTS.items():
            env.setdefault(name, value)
        env.update({str(key): str(value) for key, value in payload["env"].items()})
        try:
            shell = resolve_posix_shell(payload.get("shell"), env)
        except ShellNotFoundError as exc:
            return _process_failure("shell_not_found", str(exc))
        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _PROCESS_BOOTSTRAP,
                    shell,
                    payload["command"],
                ],
                cwd=cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            os.close(master_fd)
            return _process_failure("spawn_failed", str(exc))
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        os.set_blocking(master_fd, False)
        record = _ProcessRecord(owner, process, master_fd)
        with self.condition:
            self.records[terminal_id] = record
        threading.Thread(target=self.drain, args=(record,), daemon=True).start()
        return _process_success({"pid": process.pid})

    def poll(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Wait until a polled process changed or printed, then report each one.

        ``processes`` maps each terminal to the cursor and version its client
        holds; output before a cursor was received and is dropped here. The
        ``maxBytes`` budget is shared fairly among the processes with output.
        """
        wanted: Mapping[str, Mapping[str, int]] = payload["processes"]
        wait_seconds = min(int(payload.get("waitMs", 0)), _PROCESS_MAX_WAIT_MS) / 1_000
        budget = min(int(payload.get("maxBytes", 0)), _PROCESS_MAX_POLL_BYTES)
        deadline = time.monotonic() + wait_seconds
        with self.condition:
            for terminal_id, held in wanted.items():
                record = self.records.get(terminal_id)
                if record is not None:
                    _drop_read_output(record, int(held["cursor"]))
            # Acknowledged output makes room for processes that were held back.
            self.condition.notify_all()
            while not self._changed(wanted):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            cursors = {
                terminal_id: max(int(held["cursor"]), record.unread_start)
                for terminal_id, held in wanted.items()
                if (record := self.records.get(terminal_id)) is not None
            }
            shares = _fair_shares(
                {
                    terminal_id: max(0, self.records[terminal_id].end_cursor - cursor)
                    for terminal_id, cursor in cursors.items()
                },
                budget,
            )
            processes: dict[str, Any] = {}
            for terminal_id in wanted:
                record = self.records.get(terminal_id)
                if record is None:
                    processes[terminal_id] = {"state": "missing"}
                    continue
                cursor = cursors[terminal_id]
                offset = cursor - record.unread_start
                chunk = bytes(record.unread[offset : offset + shares[terminal_id]])
                next_cursor = cursor + len(chunk)
                processes[terminal_id] = {
                    "state": "live",
                    "outputBase64": base64.b64encode(chunk).decode("ascii"),
                    "cursor": next_cursor,
                    "version": record.version,
                    "rootExitCode": record.root_exit_code,
                    "groupAlive": record.group_alive,
                    "ended": record.ended and next_cursor == record.end_cursor,
                }
        return _process_success({"processes": processes})

    def _changed(self, wanted: Mapping[str, Mapping[str, int]]) -> bool:
        for terminal_id, held in wanted.items():
            record = self.records.get(terminal_id)
            if (
                record is None
                or record.version != int(held["version"])
                or record.end_cursor > int(held["cursor"])
            ):
                return True
        return False

    def write(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self.condition:
            record = self.records.get(str(payload["terminalId"]))
        if record is None:
            return _process_failure("not_running", "Process is not running")
        data = base64.b64decode(str(payload["dataBase64"]))
        with record.terminal_lock:
            if record.terminal_closed:
                return _process_failure("not_running", "Process is not running")
            try:
                written = _write_terminal(record.master_fd, data)
            except OSError as exc:
                return _process_failure("write_failed", str(exc))
        return _process_success({"bytesWritten": written})

    def stop(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Stop processes and what descends from them, all in this one request.

        Sends SIGTERM, then SIGKILL to what is left after ``graceMs``, and
        answers once everything is gone or a second grace ran out.
        """
        grace_seconds = min(int(payload["graceMs"]), _PROCESS_MAX_GRACE_MS) / 1_000
        with self.condition:
            found = {
                str(terminal_id): self.records[str(terminal_id)]
                for terminal_id in payload["terminalIds"]
                if str(terminal_id) in self.records
            }
        contained = stop_process_trees(
            [record.tree for record in found.values()], grace_seconds
        )
        processes: dict[str, Any] = {
            str(terminal_id): {"state": "missing"}
            for terminal_id in payload["terminalIds"]
        }
        for index, (terminal_id, record) in enumerate(found.items()):
            self._observe(record)
            with self.condition:
                processes[terminal_id] = {
                    "state": "live",
                    "version": record.version,
                    "rootExitCode": record.root_exit_code,
                    "groupAlive": record.group_alive,
                    "stopped": contained[index],
                }
        return _process_success({"processes": processes})

    def shut_down(self, owner: str) -> dict[str, Any]:
        """Stop everything and leave, unless a later client replaced ``owner``.

        A client the server never heard from takes over first: a run that
        resumes a Session and starts nothing still stops what the run before
        it left. A replaced client's late shutdown stops nothing.
        """
        with self.condition:
            if owner in self.past_owners:
                return _process_success({})
            self.closing = True
        self.take_over(owner)
        self.stop_all()
        self._leave()
        return _process_success({})

    def stop_all(self) -> None:
        with self.condition:
            records = list(self.records.values())
        stop_process_trees(
            [record.tree for record in records], _PROCESS_STOP_GRACE_SECONDS
        )

    def watch_idle(self) -> None:
        """Check :meth:`leave_if_idle` every second until the server leaves."""
        while not self.leave_if_idle():
            time.sleep(1.0)

    def leave_if_idle(self) -> bool:
        """Leave if no request came for long enough; return whether it is leaving.

        Leaves once nothing runs and the client was quiet for ``idle_seconds``.
        A client quiet for ``abandoned_seconds`` is gone for good: its
        processes are stopped first.
        """
        with self.condition:
            if self.closing:
                return True
            quiet = self.clock() - self.last_request
            running = any(not record.ended for record in self.records.values())
            abandoned = quiet >= self.abandoned_seconds
            if not abandoned and (running or quiet < self.idle_seconds):
                return False
            self.closing = True
        if abandoned:
            self.stop_all()
        self._leave()
        return True

    def _leave(self) -> None:
        network = self.network
        if network is not None:
            threading.Thread(target=network.shutdown, daemon=True).start()

    def drain(self, record: _ProcessRecord) -> None:
        """Collect a process's output until it and all its group have exited.

        Holds off while the client has ``unread_limit`` bytes of it to read.
        """
        import select

        while True:
            if self._backlog_is_full(record):
                self._observe(record)
                continue
            readable, _, _ = select.select([record.master_fd], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(record.master_fd, 64 * 1_024)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if chunk:
                    self._append(record, chunk)
                    continue
            if not self._observe(record):
                break
            if readable:
                # Every terminal end is closed, but the process still runs.
                time.sleep(0.1)
        record.close_terminal()
        with self.condition:
            record.ended = True
            record.version += 1
            self.condition.notify_all()

    def _backlog_is_full(self, record: _ProcessRecord) -> bool:
        with self.condition:
            if record.discarded or len(record.unread) < self.unread_limit:
                return False
            self.condition.wait(0.1)
            return True

    def _append(self, record: _ProcessRecord, chunk: bytes) -> None:
        with self.condition:
            if record.discarded:
                return
            record.unread.extend(chunk)
            self.condition.notify_all()

    def _observe(self, record: _ProcessRecord) -> bool:
        """Record whether the process runs, and return it."""
        now = time.monotonic()
        if now - record.tracked_at >= _PROCESS_TRACK_SECONDS:
            record.tracked_at = now
            record.tree.track(self._recent_snapshot())
        root_exit_code = record.process.poll()
        group_alive = record.tree.group_is_alive()
        with self.condition:
            if (
                record.root_exit_code != root_exit_code
                or record.group_alive != group_alive
            ):
                record.root_exit_code = root_exit_code
                record.group_alive = group_alive
                record.version += 1
                self.condition.notify_all()
        return root_exit_code is None or group_alive

    def _recent_snapshot(self) -> _ProcessSnapshot:
        # One process table serves every process's periodic track: reading it
        # is a /proc scan, or a ps run where there is no /proc. Stops read
        # their own, so they see descendants started since.
        with self._snapshot_lock:
            if (
                self._snapshot is None
                or time.monotonic() - self._snapshot[0] >= _PROCESS_TRACK_SECONDS
            ):
                self._snapshot = read_process_snapshot()
            return self._snapshot


class _ProcessTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    process_server: ProcessServer


class _ProcessRequestHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        self.request.settimeout(_PROCESS_REQUEST_READ_SECONDS)
        super().setup()

    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _ProcessTCPServer)
        try:
            raw = self.rfile.readline(_PROCESS_FRAME_BYTES + 1)
            frame = json.loads(raw.decode("utf-8"))
            if not raw.endswith(b"\n") or not isinstance(frame, dict):
                raise ValueError("frame must be one JSON object and a newline")
            self.request.settimeout(None)
            response = server.process_server.dispatch(frame)
        except (OSError, ValueError) as exc:
            response = _process_failure("invalid_request", str(exc))
        except (KeyError, TypeError) as exc:
            response = _process_failure("invalid_request", f"Malformed request: {exc}")
        self.wfile.write(
            json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n"
        )
        self.wfile.flush()


def _process_success(value: Any) -> dict[str, Any]:
    return {"outcome": "succeeded", "value": value}


def _process_failure(code: str, message: str) -> dict[str, Any]:
    return {"outcome": "failed", "code": code, "message": message}


def _drop_read_output(record: _ProcessRecord, cursor: int) -> None:
    received = min(cursor, record.end_cursor) - record.unread_start
    if received > 0:
        del record.unread[:received]
        record.unread_start += received


def _fair_shares(available: Mapping[str, int], budget: int) -> dict[str, int]:
    """Split ``budget`` so no process's output waits behind another's."""
    shares: dict[str, int] = {}
    remaining = budget
    ordered = sorted(available, key=available.__getitem__)
    for position, key in enumerate(ordered):
        share = min(available[key], remaining // (len(ordered) - position))
        shares[key] = share
        remaining -= share
    return shares


def _write_terminal(master_fd: int, data: bytes) -> int:
    import select

    remaining = memoryview(data)
    total = 0
    deadline = time.monotonic() + _PROCESS_WRITE_SECONDS
    while remaining:
        try:
            written = os.write(master_fd, remaining)
        except BlockingIOError:
            written = 0
        total += written
        remaining = remaining[written:]
        timeout = deadline - time.monotonic()
        if not remaining or timeout <= 0:
            break
        _, writable, _ = select.select([], [master_fd], [], timeout)
        if not writable:
            break
    return total


def _process_server_endpoint(directory: str, session_id: str) -> dict[str, Any]:
    endpoint = _read_endpoint(directory)
    if endpoint is not None and _answers(endpoint, session_id):
        return endpoint
    with _startup_lock(directory):
        endpoint = _read_endpoint(directory)
        if endpoint is not None and _answers(endpoint, session_id):
            return endpoint
        _remove_unowned_endpoint(directory)
        return _start_process_server(directory, session_id)


def _start_process_server(directory: str, session_id: str) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    log = os.open(
        os.path.join(directory, "server.log"),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                os.path.abspath(__file__),
                PROCESS_SERVER_ARGUMENT,
                "--session-id",
                session_id,
            ],
            cwd=directory,
            env={**os.environ, _PROCESS_TOKEN_ENV: token},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
    finally:
        os.close(log)
    deadline = time.monotonic() + _PROCESS_STARTUP_SECONDS
    while time.monotonic() < deadline:
        endpoint = _read_endpoint(directory)
        if (
            endpoint is not None
            and endpoint.get("pid") == process.pid
            and _answers(endpoint, session_id)
        ):
            return endpoint
        if process.poll() is not None:
            break
        time.sleep(0.02)
    if process.poll() is None:
        process.kill()
        process.wait()
    raise OSError(f"Background process server did not start: {_log_tail(directory)}")


def _shut_down_process_server(
    directory: str, frame: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    endpoint = _read_endpoint(directory)
    if endpoint is None:
        return _process_success({})
    try:
        response = _send_process_frame(endpoint, frame, timeout)
    except _RefusedAtConnectError:
        with _startup_lock(directory):
            _remove_unowned_endpoint(directory)
        return _process_success({})
    deadline = time.monotonic() + _PROCESS_STARTUP_SECONDS
    while time.monotonic() < deadline:
        current = _read_endpoint(directory)
        if current is None or current.get("pid") != endpoint.get("pid"):
            break
        time.sleep(0.02)
    return response


def _answers(endpoint: Mapping[str, Any], session_id: str) -> bool:
    if endpoint.get("protocolVersion") != _PROCESS_PROTOCOL_VERSION:
        return False
    try:
        response = _send_process_frame(
            endpoint,
            {"type": "handshake", "sessionId": session_id},
            _PROCESS_CONNECT_SECONDS,
        )
    except (OSError, ValueError):
        return False
    return response.get("outcome") == "succeeded"


def _send_process_frame(
    endpoint: Mapping[str, Any], frame: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    try:
        connection = socket.create_connection(
            (str(endpoint["host"]), int(endpoint["port"])),
            timeout=_PROCESS_CONNECT_SECONDS,
        )
    except ConnectionRefusedError as exc:
        raise _RefusedAtConnectError(exc.errno, str(exc)) from exc
    with connection:
        connection.settimeout(timeout)
        message = {**frame, "token": endpoint["token"]}
        connection.sendall(json.dumps(message).encode("utf-8") + b"\n")
        with connection.makefile("rb") as stream:
            raw = stream.readline(_PROCESS_FRAME_BYTES + 1)
    if not raw.endswith(b"\n"):
        raise OSError("Background process server closed the connection")
    response = json.loads(raw.decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("Background process server sent a malformed response")
    return response


@contextlib.contextmanager
def _startup_lock(directory: str) -> Iterator[None]:
    import fcntl

    descriptor = os.open(
        os.path.join(directory, "startup.lock"), os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _remove_unowned_endpoint(directory: str) -> None:
    """Forget the recorded endpoint unless a running server holds its lock.

    Only under the startup lock: a server starting meanwhile would lose its
    endpoint.
    """
    import fcntl

    descriptor = os.open(
        os.path.join(directory, "server.lock"), os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with contextlib.suppress(FileNotFoundError):
            os.unlink(os.path.join(directory, "endpoint.json"))
    finally:
        os.close(descriptor)


def _read_endpoint(directory: str) -> dict[str, Any] | None:
    try:
        with open(os.path.join(directory, "endpoint.json"), encoding="utf-8") as handle:
            endpoint = json.load(handle)
    except (OSError, ValueError):
        return None
    return endpoint if isinstance(endpoint, dict) else None


def _remove_own_endpoint(path: str, pid: int) -> None:
    try:
        with open(path, encoding="utf-8") as handle:
            endpoint = json.load(handle)
    except (OSError, ValueError):
        return
    if isinstance(endpoint, dict) and endpoint.get("pid") == pid:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def _write_private_json(path: str, value: Mapping[str, Any]) -> None:
    temporary = f"{path}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _log_tail(directory: str) -> str:
    try:
        with open(
            os.path.join(directory, "server.log"), encoding="utf-8", errors="replace"
        ) as handle:
            return handle.read()[-2_000:].strip() or "no output"
    except OSError:
        return "no output"


def _process_state_directory(session_id: str) -> str:
    """The Session's private state directory, created if needed.

    Under ``$XDG_STATE_HOME`` (or ``~/.local/state``), else under the temporary
    directory; owned by this user and closed to everyone else.
    """
    name = "session-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    try:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            str(Path.home()), ".local", "state"
        )
        root = _private_directory(os.path.join(base, _PROCESS_STATE_ROOT))
    except (OSError, KeyError, RuntimeError):
        root = _private_directory(
            os.path.join(tempfile.gettempdir(), _PROCESS_FALLBACK_STATE_ROOT)
        )
    return _private_directory(os.path.join(root, name))


def _private_directory(path: str) -> str:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.geteuid():
            raise PermissionError(f"State directory is not this user's: {path}")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return path


# Script entry point


def encode_request(request: Mapping[str, Any]) -> str:
    """Encode a request for the ``--request-base64`` argument."""
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(payload)).decode("ascii")


def decode_request(encoded: str) -> dict[str, Any]:
    payload = zlib.decompress(base64.urlsafe_b64decode(encoded.encode("ascii")))
    request = json.loads(payload.decode("utf-8"))
    if not isinstance(request, dict):
        raise TypeError("request must be a JSON object")
    return request


def _run_operation(operation: str, request: Mapping[str, Any]) -> Any:
    if operation == "file":
        return run_file_tool(request)
    if operation == "save-tool-result":
        return save_tool_result(request)
    if operation == "skills-scan":
        return scan_skills(request)
    if operation == "skills-install":
        return install_skills(request)
    if operation == "bash":
        return asyncio.run(
            run_bash(
                request["command"],
                timeout_seconds=request["timeout_seconds"],
                cwd=request["cwd"],
                shell=request.get("shell"),
            )
        )
    if operation == "process":
        return run_process_operation(request)
    raise ValueError(f"Unsupported sandbox helper operation: {operation}")


def take_request_file(path: str) -> str:
    """Read a payload streamed into ``path``, and delete the file."""
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def main(argv: Sequence[str]) -> int:
    """Run one operation and print its JSON response; exit 0 unless unusable."""
    if argv and argv[0] == PROCESS_SERVER_ARGUMENT:
        return serve_processes(argv)
    if len(argv) != 3:  # noqa: PLR2004
        return _usage()
    if argv[1] == "--request-base64":
        encoded = argv[2]
    elif argv[1] == "--request-file":
        encoded = take_request_file(argv[2])
    else:
        return _usage()
    request = decode_request(encoded)
    try:
        response: dict[str, Any] = {
            "ok": True,
            "result": _run_operation(argv[0], request),
        }
    except (OSError, UnicodeError, ValueError, ShellNotFoundError, TimeoutError) as exc:
        response = {"ok": False, "error": str(exc)}
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    sys.stdout.flush()
    return 0


def _usage() -> int:
    sys.stderr.write(
        "usage: <operation> (--request-base64 <payload> | --request-file <path>)\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
