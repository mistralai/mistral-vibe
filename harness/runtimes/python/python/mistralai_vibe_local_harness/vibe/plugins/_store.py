"""Content-addressed storage for pinned plugin packages.

Every call here blocks. ``ingest`` hashes a whole tree and ``checkout`` rebuilds
one, so a Session Runtime call site offloads with ``asyncio.to_thread`` rather
than stalling the loop for every other session in the process.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from mistralai_vibe_local_harness.vibe._storage import Sha256, canonical_json

MANIFEST_VERSION = 1

# A backstop against one pathological root filling the store, not a budget.
MAX_PACKAGE_ENTRIES = 10_000
MAX_PACKAGE_BYTES = 256 * 1024 * 1024

# Long enough that no in-flight ingest is ever mistaken for abandoned work.
TEMPORARY_TTL_SECONDS = 24 * 60 * 60

_READ_CHUNK_BYTES = 1 << 20
_BLOB_MODE = 0o400
_WRITABLE_DIRECTORY_MODE = 0o700
_CHECKOUT_DIRECTORY_MODE = 0o500


class PluginPackageError(RuntimeError):
    """Base class for every failure the package store raises."""


class PluginPackageInvalidTree(PluginPackageError):
    """The tree cannot be pinned: it is not a directory, or an entry is not portable."""


class PluginPackageTooLarge(PluginPackageError):
    """The tree exceeds the store's entry count or byte budget."""


class PluginPackageMismatch(PluginPackageError):
    """The tree digests to something other than the digest the caller declared."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Plugin package digests to {actual}, not the requested {expected}")


class PluginPackageUnavailable(PluginPackageError):
    """No manifest, or no blob, for this content digest."""

    def __init__(self, content_digest: str, detail: str) -> None:
        self.content_digest = content_digest
        super().__init__(f"Plugin package {content_digest} cannot be rebuilt: {detail}")


class PluginPackageCorrupt(PluginPackageError):
    """The stored bytes for this content digest no longer produce it."""

    def __init__(self, content_digest: str, detail: str) -> None:
        self.content_digest = content_digest
        super().__init__(f"Plugin package {content_digest} is corrupt: {detail}")


class PluginBlobUnavailable(PluginPackageError):
    """No blob for this digest."""

    def __init__(self, digest: str) -> None:
        self.digest = digest
        super().__init__(f"Blob is not in the store: {digest}")


class _StoredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_entry_path(value: str) -> str:
    """Keep a checkout inside its own directory even if a manifest is tampered with."""
    pure = PurePosixPath(value)
    if (
        value in {"", ".", ".."}
        or value.startswith("/")
        or "\\" in value
        or pure.is_absolute()
        or ".." in pure.parts
    ):
        raise ValueError("package entry path must be a relative POSIX path inside the tree")
    return value


type PackagePath = Annotated[str, AfterValidator(_validate_entry_path)]


class PackageFileV1(_StoredModel):
    kind: Literal["file"] = "file"
    path: PackagePath
    sha256: Sha256


class PackageSymlinkV1(_StoredModel):
    kind: Literal["symlink"] = "symlink"
    path: PackagePath
    target: str = Field(min_length=1)


PackageEntryV1 = Annotated[PackageFileV1 | PackageSymlinkV1, Field(discriminator="kind")]


class PackageManifestV1(_StoredModel):
    manifest_version: Literal[1] = MANIFEST_VERSION
    content_digest: Sha256
    entries: list[PackageEntryV1]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        paths = [entry.path for entry in self.entries]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("package entry paths must be sorted and unique")
        return self


_MANIFEST_ADAPTER = TypeAdapter(PackageManifestV1)


type IgnoredNames = frozenset[str]


type _EntryKind = Literal["file", "symlink", "unsupported"]

# The one-byte tag each kind contributes to a tree digest. Frozen: changing a
# tag rekeys every package already in a store.
_DIGEST_TAGS: dict[_EntryKind, bytes] = {
    "file": b"f",
    "symlink": b"l",
    "unsupported": b"o",
}


@dataclass(frozen=True, slots=True)
class _ScannedEntry:
    """One tree entry: a file's digest, a symlink's target, or neither."""

    kind: _EntryKind
    path: str
    payload: str


def digest_plugin_tree(root: Path, *, ignored_names: IgnoredNames = frozenset()) -> Sha256:
    """Digest a plugin tree without a store.

    Public because the Host computes ``AgentConfig.plugins[].contentDigest``
    before it sends ``session/start``, and both sides must run one
    implementation of the algorithm rather than two kept in step by hand.
    """
    return _digest_entries(_scan_tree(root, ignored_names))


class PluginPackageStore:
    """A content-addressed store for plugin package trees and snapshot bytes.

    ``blobs/`` holds file contents and snapshots keyed by their own sha256,
    ``manifests/`` holds one tree description per package, and ``packages/``
    holds read-only checkouts that hard-link into ``blobs/``.
    """

    def __init__(self, storage_root: Path) -> None:
        self.root = Path(storage_root) / "plugins"
        self._blobs = self.root / "blobs"
        self._manifests = self.root / "manifests"
        self._packages = self.root / "packages"
        self._tmp = self.root / "tmp"
        self._sweep_temporaries()

    def ingest(
        self,
        root: Path,
        *,
        expected: Sha256 | None = None,
        ignored_names: IgnoredNames = frozenset(),
    ) -> Sha256:
        """Digest the tree, write every new blob and the manifest. Idempotent.

        Raises ``PluginPackageMismatch`` when ``expected`` is given and differs,
        which turns the race between the Host's resolve and this ingest into a
        typed failure rather than a silently different pin.
        """
        scanned = _scan_tree(root, ignored_names)
        unsupported = [entry.path for entry in scanned if entry.kind == "unsupported"]
        if unsupported:
            raise PluginPackageInvalidTree(
                "plugin tree holds entries that are neither files nor symbolic links "
                f"and cannot be rebuilt: {', '.join(unsupported[:5])}"
            )
        digest = _digest_entries(scanned)
        if expected is not None and expected != digest:
            raise PluginPackageMismatch(expected, digest)

        shards: set[Path] = set()
        for entry in scanned:
            if entry.kind != "file" or self.has(entry.payload):
                continue
            source = root / PurePosixPath(entry.path)
            self._publish_blob(entry.payload, lambda path, source=source: _copy_file(source, path))
            shards.add(self._blob_path(entry.payload).parent)
        for shard in sorted(shards):
            _fsync_directory(shard)

        manifest = PackageManifestV1(
            content_digest=digest,
            entries=[
                PackageFileV1(path=entry.path, sha256=entry.payload)
                if entry.kind == "file"
                else PackageSymlinkV1(path=entry.path, target=entry.payload)
                for entry in scanned
            ],
        )
        self._write_manifest(manifest)
        return digest

    def checkout(self, content_digest: Sha256) -> Path:
        """Rebuild the tree read-only at ``packages/<ab>/<digest>/`` and return it.

        Raises ``PluginPackageUnavailable`` if the manifest or any blob is
        missing, and ``PluginPackageCorrupt`` if the rebuilt tree does not
        re-digest to its key.
        """
        final = self._package_path(content_digest)
        if final.is_dir():
            return final
        manifest = self._read_manifest(content_digest)
        staging = self._temporary_path(f"package-{content_digest[:16]}")
        try:
            staging.mkdir(mode=_WRITABLE_DIRECTORY_MODE, parents=True)
            for entry in manifest.entries:
                target = staging / PurePosixPath(entry.path)
                target.parent.mkdir(mode=_WRITABLE_DIRECTORY_MODE, parents=True, exist_ok=True)
                if isinstance(entry, PackageSymlinkV1):
                    os.symlink(entry.target, target)
                    continue
                blob = self._blob_path(entry.sha256)
                if not blob.is_file():
                    raise PluginPackageUnavailable(
                        content_digest, f"no blob for {entry.path} ({entry.sha256})"
                    )
                _link_or_copy(blob, target)
            rebuilt = _digest_entries(_scan_tree(staging))
            if rebuilt != content_digest:
                raise PluginPackageCorrupt(content_digest, f"the rebuilt tree digests to {rebuilt}")
            final.parent.mkdir(mode=_WRITABLE_DIRECTORY_MODE, parents=True, exist_ok=True)
            try:
                os.rename(staging, final)
            except OSError:
                # Another process published the identical tree first.
                if not final.is_dir():
                    raise
                return final
            _harden_directories(final)
            return final
        finally:
            if staging.exists():
                _remove_tree(staging)

    def put_blob(self, payload: bytes) -> Sha256:
        digest = hashlib.sha256(payload).hexdigest()
        if not self.has(digest):
            self._publish_blob(digest, lambda path: _write_bytes(path, payload))
            _fsync_directory(self._blob_path(digest).parent)
        return digest

    def read_blob(self, digest: Sha256) -> bytes:
        path = self._blob_path(digest)
        if not path.is_file():
            raise PluginBlobUnavailable(digest)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise PluginPackageCorrupt(digest, "the stored blob does not digest to its key")
        return payload

    def has(self, digest: Sha256) -> bool:
        """Whether a blob with this digest is stored. Packages are keyed separately."""
        return self._blob_path(digest).is_file()

    def _blob_path(self, digest: str) -> Path:
        return self._blobs / digest[:2] / digest

    def _manifest_path(self, digest: str) -> Path:
        return self._manifests / digest[:2] / digest

    def _package_path(self, digest: str) -> Path:
        return self._packages / digest[:2] / digest

    def _temporary_path(self, label: str) -> Path:
        self._tmp.mkdir(mode=_WRITABLE_DIRECTORY_MODE, parents=True, exist_ok=True)
        return self._tmp / f"{label}-{secrets.token_hex(8)}"

    def _publish_blob(self, digest: str, write: Callable[[Path], None]) -> None:
        path = self._blob_path(digest)
        path.parent.mkdir(mode=_WRITABLE_DIRECTORY_MODE, parents=True, exist_ok=True)
        temporary = self._temporary_path(f"blob-{digest[:16]}")
        try:
            write(temporary)
            _fsync_file(temporary)
            os.chmod(temporary, _BLOB_MODE)
            try:
                os.rename(temporary, path)
            except OSError:
                # Losing the race is the same outcome: the bytes are their key.
                if not path.is_file():
                    raise
        finally:
            temporary.unlink(missing_ok=True)

    def _write_manifest(self, manifest: PackageManifestV1) -> None:
        path = self._manifest_path(manifest.content_digest)
        if path.is_file():
            return
        path.parent.mkdir(mode=_WRITABLE_DIRECTORY_MODE, parents=True, exist_ok=True)
        payload = canonical_json(manifest.model_dump(mode="json")) + b"\n"
        temporary = self._temporary_path(f"manifest-{manifest.content_digest[:16]}")
        try:
            temporary.write_bytes(payload)
            _fsync_file(temporary)
            os.chmod(temporary, _BLOB_MODE)
            try:
                os.rename(temporary, path)
            except OSError:
                if not path.is_file():
                    raise
            _fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_manifest(self, content_digest: str) -> PackageManifestV1:
        path = self._manifest_path(content_digest)
        if not path.is_file():
            raise PluginPackageUnavailable(content_digest, "no manifest")
        try:
            manifest = _MANIFEST_ADAPTER.validate_json(path.read_bytes())
        except Exception as exc:
            raise PluginPackageCorrupt(content_digest, f"unreadable manifest: {exc}") from exc
        # A manifest naming entries that hash elsewhere would rebuild a tree
        # nobody pinned, so the key is re-derived rather than trusted.
        if manifest.content_digest != content_digest:
            raise PluginPackageCorrupt(content_digest, "the manifest names another package")
        if _digest_entries(_manifest_entries(manifest)) != content_digest:
            raise PluginPackageCorrupt(content_digest, "the manifest entries do not digest to it")
        return manifest

    def _sweep_temporaries(self) -> None:
        """Drop whatever a crash left under ``tmp/``, which is never load-bearing.

        Only stale entries: a concurrent ingest's staging directory differs
        from an abandoned one by age alone.
        """
        if not self._tmp.is_dir():
            return
        cutoff = time.time() - TEMPORARY_TTL_SECONDS
        for leftover in self._tmp.iterdir():
            try:
                if leftover.lstat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            _remove_tree(leftover)


def _manifest_entries(manifest: PackageManifestV1) -> list[_ScannedEntry]:
    return [
        _ScannedEntry("file", entry.path, entry.sha256)
        if isinstance(entry, PackageFileV1)
        else _ScannedEntry("symlink", entry.path, entry.target)
        for entry in manifest.entries
    ]


def _digest_entries(entries: Sequence[_ScannedEntry]) -> str:
    """Frame each entry so the digest is injective over the tree.

    Without the tag and the NUL separators a file named ``a\\0b`` could collide
    with two entries. Mode bits are excluded on purpose: Windows has no POSIX
    mode, and including them would make the key platform-dependent.
    """
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(_DIGEST_TAGS[entry.kind])
        digest.update(b"\0")
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        if entry.kind == "unsupported":
            continue
        payload = (
            entry.payload
            if entry.kind == "file"
            else hashlib.sha256(entry.payload.encode("utf-8")).hexdigest()
        )
        digest.update(payload.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _scan_tree(root: Path, ignored_names: IgnoredNames = frozenset()) -> list[_ScannedEntry]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise PluginPackageInvalidTree(f"plugin package root is not a directory: {root}")
    entries: list[_ScannedEntry] = []
    total_bytes = 0
    pending: list[tuple[Path, str]] = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                if child.name in ignored_names:
                    continue
                path = _entry_path(prefix, child.name)
                if child.is_symlink():
                    entries.append(_ScannedEntry("symlink", path, os.readlink(child.path)))
                elif child.is_dir():
                    pending.append((Path(child.path), path))
                    continue
                elif child.is_file():
                    total_bytes += child.stat().st_size
                    entries.append(_ScannedEntry("file", path, _digest_file(Path(child.path))))
                else:
                    entries.append(_ScannedEntry("unsupported", path, ""))
                if len(entries) > MAX_PACKAGE_ENTRIES:
                    raise PluginPackageTooLarge(
                        f"plugin tree holds more than {MAX_PACKAGE_ENTRIES} entries: {root}"
                    )
                if total_bytes > MAX_PACKAGE_BYTES:
                    raise PluginPackageTooLarge(
                        f"plugin tree holds more than {MAX_PACKAGE_BYTES} bytes: {root}"
                    )
    entries.sort(key=lambda entry: entry.path)
    _reject_normalization_collisions(entries)
    return entries


def _entry_path(prefix: str, name: str) -> str:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PluginPackageInvalidTree(
            f"plugin tree holds a name that is not valid UTF-8: {name!r}"
        ) from exc
    # NFC, matching the snapshot's rule, so one tree digests identically on
    # macOS and on Linux.
    return unicodedata.normalize("NFC", f"{prefix}/{name}" if prefix else name)


def _reject_normalization_collisions(entries: Iterable[_ScannedEntry]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.path in seen:
            raise PluginPackageInvalidTree(
                f"plugin tree holds two entries whose names normalize to one path: {entry.path!r}"
            )
        seen.add(entry.path)


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(target: Path, payload: bytes) -> None:
    target.write_bytes(payload)


def _copy_file(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("wb") as writer:
        while chunk := reader.read(_READ_CHUNK_BYTES):
            writer.write(chunk)


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
        return
    except OSError:
        # No hard links here, or two volumes. The rebuild is verified either
        # way, so the fallback costs disk and nothing else.
        _copy_file(source, target)
        os.chmod(target, _BLOB_MODE)


def _harden_directories(root: Path) -> None:
    """Make a published checkout read-only, deepest directory first.

    Files arrive read-only already, since a hard link shares the blob's mode.
    Removing a hardened checkout needs :func:`_remove_tree`.
    """
    for directory, _names, _files in os.walk(root, topdown=False):
        os.chmod(directory, _CHECKOUT_DIRECTORY_MODE)
    os.chmod(root, _CHECKOUT_DIRECTORY_MODE)


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    for directory, _names, _files in os.walk(path, topdown=False):
        os.chmod(directory, _WRITABLE_DIRECTORY_MODE)
    os.chmod(path, _WRITABLE_DIRECTORY_MODE)
    shutil.rmtree(path, ignore_errors=True)


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as file:
        os.fsync(file.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Windows directory handles do not support the POSIX fsync protocol.
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "MAX_PACKAGE_BYTES",
    "MAX_PACKAGE_ENTRIES",
    "IgnoredNames",
    "PackageFileV1",
    "PackageManifestV1",
    "PackageSymlinkV1",
    "PluginBlobUnavailable",
    "PluginPackageCorrupt",
    "PluginPackageError",
    "PluginPackageInvalidTree",
    "PluginPackageMismatch",
    "PluginPackageStore",
    "PluginPackageTooLarge",
    "PluginPackageUnavailable",
    "TEMPORARY_TTL_SECONDS",
    "digest_plugin_tree",
]
