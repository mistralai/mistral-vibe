"""Per-repo record of active (skill_id, version) pins in the shared store.

The registry cache under ``$VIBE_HOME`` is shared across every repository on the
machine, but a single sync only sees its own manifests. Without a record of what
*other* repos pin, pruning one repo's superseded versions would evict a sibling
repo's still-pinned version from the shared store.

Each repo (keyed by its resolved project roots, or ``global`` when it has none)
records the set of versions it currently keeps active. Prune then targets the
*union* across all recorded repos, so one repo's refresh never drops another's
pinned version. A repo overwrites its own entry on every sync, so dropping a pin
removes that version from the union unless another repo still pins it.

Each repo owns a separate file, so two Vibe processes syncing different
repositories never read-modify-write the same document and cannot drop each
other's entry.

Kept synchronous on purpose: the sync engine offloads the write via
``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import os
from pathlib import Path
import tempfile

from vibe.core.config.harness_files._paths import GLOBAL_REGISTRY_SKILLS_CACHE_DIR

GLOBAL_KEY = "global"
_GLOBAL_KEY = GLOBAL_KEY


def repo_key(roots: Sequence[Path] | None) -> str:
    """A stable key for the repo identified by ``roots`` (``global`` if none)."""
    if not roots:
        return _GLOBAL_KEY
    parts = sorted(str(Path(r).resolve()) for r in roots)
    return hashlib.sha1("\n".join(parts).encode(), usedforsecurity=False).hexdigest()


def ledger_root() -> Path:
    return GLOBAL_REGISTRY_SKILLS_CACHE_DIR.path / "ledger"


def record(key: str, active: set[tuple[str, int]]) -> None:
    """Persist ``key``'s active (skill_id, version) set (replaces that key).

    An empty set drops the entry, so a repo that no longer pins anything stops
    holding versions in the union.
    """
    root = ledger_root()
    if not active:
        (root / f"{key}.txt").unlink(missing_ok=True)
        return
    root.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sorted(f"{sid}@{version}" for sid, version in active))
    handle, tmp_name = tempfile.mkstemp(dir=root, prefix=f"{key}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            tmp.write(body)
        os.replace(tmp_name, root / f"{key}.txt")
    except OSError:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def union() -> set[tuple[str, int]]:
    """The union of every recorded repo's active (skill_id, version) set."""
    out: set[tuple[str, int]] = set()
    try:
        entries = sorted(ledger_root().glob("*.txt"))
    except OSError:
        return out
    for path in entries:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for item in body.splitlines():
            skill_id, sep, version = item.strip().rpartition("@")
            if sep and skill_id and version.isdigit():
                out.add((skill_id, int(version)))
    return out
