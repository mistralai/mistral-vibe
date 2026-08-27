from __future__ import annotations

import hashlib
from pathlib import Path

from vibe.core.plugins._canonical import normalize_nfc

_CHUNK = 1024 * 1024


def digest_plugin_tree(
    root: Path, *, ignored_names: frozenset[str] = frozenset()
) -> str:
    """Digest a plugin tree with the framing the package store ingests.

    Each entry is ``<tag> NUL <posix relative path> NUL <sha256> NUL``, in
    path-sorted order. What is folded in is the digest of the content, never
    the content: a path and a symlink target cannot contain NUL, so the
    separator delimits them, but a file's bytes can, and a file spelling out
    the framing of the entries behind it would otherwise hash the same as the
    tree it imitates. A fixed-width hex digest is unambiguous whatever the
    bytes behind it.

    Paths are NFC-normalized, so one tree digests identically on macOS and
    Linux. Mode bits are excluded: Windows has no POSIX mode, and a plugin
    declares its interpreter rather than relying on the executable bit.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: _relative(root, item)):
        if not ignored_names.isdisjoint(path.relative_to(root).parts):
            continue
        relative = _relative(root, path)
        if path.is_symlink():
            _entry(digest, b"l", relative, _digest(str(path.readlink()).encode()))
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            # A socket or a device node has no content to pin, but its presence
            # is part of the tree, so the entry is recorded without a digest.
            _entry(digest, b"o", relative)
            continue
        _entry(digest, b"f", relative, _digest_file(path))
    return digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    return normalize_nfc(path.relative_to(root).as_posix())


def _entry(digest: hashlib._Hash, tag: bytes, relative: str, content: str = "") -> None:
    digest.update(tag)
    digest.update(b"\0")
    digest.update(relative.encode())
    digest.update(b"\0")
    if content:
        digest.update(content.encode())
        digest.update(b"\0")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
