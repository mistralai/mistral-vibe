"""Disk usage on macOS with ``getattrlistbulk(2)``: one call returns a batch of
entries with their private size, the blocks no other file shares.

``du`` counts the blocks a copy-on-write clone shares with its template once
per file, about a hundred times what a fresh worktree really adds. The private
size counts them nowhere, so the template is measured by allocated size
instead (``sizes.measure``), and every shared block counts once, there.
"""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import struct

from vibe.app_server._storage_usage.sizes import Budget, Measured, SeenFiles

_ATTR_BIT_MAP_COUNT = 5
_ATTR_CMN_NAME = 0x00000001
_ATTR_CMN_DEVID = 0x00000002
_ATTR_CMN_OBJTYPE = 0x00000008
_ATTR_CMN_MODTIME = 0x00000400
_ATTR_CMN_FILEID = 0x02000000
_ATTR_CMN_ERROR = 0x20000000
_ATTR_CMN_RETURNED_ATTRS = 0x80000000
_ATTR_FILE_LINKCOUNT = 0x00000001
_ATTR_FILE_ALLOCSIZE = 0x00000004
_ATTR_CMNEXT_PRIVATESIZE = 0x00000008
_FSOPT_NOFOLLOW = 0x00000001
_FSOPT_PACK_INVAL_ATTRS = 0x00000008
_FSOPT_ATTR_CMN_EXTENDED = 0x00000020
_VDIR = 2
_BUFFER_BYTES = 256 * 1024

# Packed in this order: the returned set and the error first, then every
# requested attribute by group and bit value, each 4-byte aligned. With
# FSOPT_PACK_INVAL_ATTRS an attribute that does not apply is packed as zeros,
# so the layout is the same for every entry.
_HEADER = struct.Struct("=I5II")  # length, returned set, error
_NAME = struct.Struct("=iI")  # attrreference_t: offset from itself, length
# devid, objtype, modtime (seconds, nanoseconds), fileid, links, alloc
_BODY = struct.Struct("=iIqqQIq")
_PRIVATE = struct.Struct("=q")


class _AttrList(ctypes.Structure):
    _fields_ = (
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    )


_REQUEST = _AttrList(
    bitmapcount=_ATTR_BIT_MAP_COUNT,
    commonattr=(
        _ATTR_CMN_RETURNED_ATTRS
        | _ATTR_CMN_NAME
        | _ATTR_CMN_DEVID
        | _ATTR_CMN_ERROR
        | _ATTR_CMN_OBJTYPE
        | _ATTR_CMN_MODTIME
        | _ATTR_CMN_FILEID
    ),
    fileattr=_ATTR_FILE_LINKCOUNT | _ATTR_FILE_ALLOCSIZE,
    forkattr=_ATTR_CMNEXT_PRIVATESIZE,
)
_OPTIONS = _FSOPT_NOFOLLOW | _FSOPT_PACK_INVAL_ATTRS | _FSOPT_ATTR_CMN_EXTENDED


type _BulkCall = Callable[..., int]


def _bulk_call() -> _BulkCall | None:
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        call = libc.getattrlistbulk
    except (OSError, AttributeError):
        return None
    call.argtypes = (
        ctypes.c_int,
        ctypes.POINTER(_AttrList),
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint64,
    )
    call.restype = ctypes.c_int
    return call


@dataclass(frozen=True, slots=True)
class _Walk:
    root_device: int
    result: Measured
    seen: SeenFiles
    pending: list[str]


@dataclass(frozen=True, slots=True)
class _Entry:
    name: bytes
    device: int
    is_dir: bool
    mtime_ms: int
    inode: int
    links: int
    allocated: int
    private: int | None

    @classmethod
    def unpack(cls, view: memoryview, start: int) -> _Entry | None:
        """None for an entry the volume could not read."""
        header = _HEADER.unpack_from(view, start)
        fork, error = header[-2], header[-1]
        if error:
            return None
        name_at = start + _HEADER.size
        name_offset, name_length = _NAME.unpack_from(view, name_at)
        name_start = name_at + name_offset
        body_at = name_at + _NAME.size
        body = _BODY.unpack_from(view, body_at)
        return cls(
            name=bytes(view[name_start : name_start + name_length]).rstrip(b"\0"),
            device=body[0],
            is_dir=body[1] == _VDIR,
            mtime_ms=body[2] * 1000 + body[3] // 1_000_000,
            inode=body[4],
            links=body[5],
            allocated=body[6],
            private=(
                _PRIVATE.unpack_from(view, body_at + _BODY.size)[0]
                if fork & _ATTR_CMNEXT_PRIVATESIZE
                else None
            ),
        )


def measure_darwin(root: Path, budget: Budget, seen: SeenFiles) -> Measured | None:
    """None when this system has no bulk reader: the caller walks portably."""
    call = _bulk_call()
    if call is None:
        return None
    result = Measured()
    try:
        root_device = root.stat(follow_symlinks=False).st_dev
    except FileNotFoundError:
        return result
    except OSError:
        result.skipped += 1
        return result
    walk = _Walk(root_device, result, seen, [str(root)])
    buffer = ctypes.create_string_buffer(_BUFFER_BYTES)
    while walk.pending:
        if budget.exhausted():
            result.complete = False
            return result
        folder = walk.pending.pop()
        try:
            descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        except OSError:
            result.skipped += 1
            continue
        try:
            _read_folder(call, descriptor, folder, buffer, walk)
        finally:
            os.close(descriptor)
    return result


def _read_folder(
    call: _BulkCall,
    descriptor: int,
    folder: str,
    buffer: ctypes.Array[ctypes.c_char],
    walk: _Walk,
) -> None:
    while True:
        count = call(descriptor, ctypes.byref(_REQUEST), buffer, len(buffer), _OPTIONS)
        if count < 0:
            # Unreadable from here on: what was read so far still counts.
            walk.result.skipped += 1
            return
        if count == 0:
            return
        # The buffer itself, not ``.raw``: that copies all 256 KB each call.
        view = memoryview(buffer)
        offset = 0
        for _ in range(count):
            entry = _Entry.unpack(view, offset)
            if entry is None:
                walk.result.skipped += 1
            else:
                _add(entry, folder, walk)
            offset += _HEADER.unpack_from(view, offset)[0]


def _add(entry: _Entry, folder: str, walk: _Walk) -> None:
    result = walk.result
    # Another device: a mount point, never part of this folder's usage.
    if entry.device != walk.root_device:
        return
    result.newest_mtime_ms = max(result.newest_mtime_ms, entry.mtime_ms)
    if entry.is_dir:
        walk.pending.append(os.path.join(folder, os.fsdecode(entry.name)))
        return
    if entry.links > 1 and not walk.seen.first(entry.device, entry.inode):
        return
    if entry.private is None:
        result.private = False
        result.bytes += entry.allocated
        return
    result.bytes += entry.private


__all__ = ["measure_darwin"]
