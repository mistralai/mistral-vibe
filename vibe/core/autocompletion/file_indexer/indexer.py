from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock

from vibe.core.autocompletion.file_indexer.ignore_rules import IgnoreRules
from vibe.core.autocompletion.file_indexer.store import (
    FileIndexStats,
    FileIndexStore,
    IndexEntry,
)
from vibe.core.autocompletion.file_indexer.watcher import Change, WatchController


@dataclass(slots=True)
class _RebuildTask:
    cancel_event: Event
    done_event: Event


class FileIndexer:
    def __init__(self, mass_change_threshold: int = 200) -> None:
        self._lock = RLock()  # guards _store snapshot access and watcher callbacks.
        self._stats = FileIndexStats()
        self._ignore_rules = IgnoreRules()
        self._store = FileIndexStore(
            self._ignore_rules, self._stats, mass_change_threshold=mass_change_threshold
        )
        self._watcher = WatchController(self._handle_watch_changes)
        self._rebuild_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="file-indexer"
        )
        self._active_rebuilds: dict[Path, _RebuildTask] = {}
        self._rebuild_lock = (
            RLock()
        )  # coordinates updates to _active_rebuilds and _target_root.
        self._target_root: Path | None = None
        self._shutdown = False

    @property
    def stats(self) -> FileIndexStats:
        return self._stats

    def get_index(self, root: Path) -> list[IndexEntry]:
        resolved_root = root.resolve()

        with self._lock:  # read current root without blocking rebuild bookkeeping
            root_changed = (
                self._store.root is not None and self._store.root != resolved_root
            )

        if root_changed:
            self._watcher.stop()
            with self._rebuild_lock:  # cancel rebuilds targeting other roots
                self._target_root = resolved_root
                for other_root, task in self._active_rebuilds.items():
                    if other_root != resolved_root:
                        task.cancel_event.set()
                        task.done_event.set()
                        self._active_rebuilds.pop(other_root, None)

        with self._lock:
            needs_rebuild = self._store.root != resolved_root

        if needs_rebuild:
            with self._rebuild_lock:
                self._target_root = resolved_root
            self._start_background_rebuild(resolved_root)
            self._wait_for_rebuild(resolved_root)

        self._watcher.start(resolved_root)

        with self._lock:  # ensure root reference is fresh before snapshotting
            return self._store.snapshot()

    def refresh(self) -> None:
        self._watcher.stop()
        with self._rebuild_lock:
            for task in self._active_rebuilds.values():
                task.cancel_event.set()
                task.done_event.set()
            self._active_rebuilds.clear()
            self._target_root = None
        with self._lock:
            self._store.clear()
            self._ignore_rules.reset()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.refresh()
        self._rebuild_executor.shutdown(wait=True)

    def __del__(self) -> None:
        if not self._shutdown:
            try:
                self.shutdown()
            except Exception:
                pass

    def _start_background_rebuild(self, root: Path) -> None:
        with self._rebuild_lock:  # one rebuild per root
            if root in self._active_rebuilds:
                return

            cancel_event = Event()
            done_event = Event()
            self._active_rebuilds[root] = _RebuildTask(
                cancel_event=cancel_event, done_event=done_event
            )

        try:
            self._rebuild_executor.submit(
                self._rebuild_worker, root, self._active_rebuilds[root]
            )
        except RuntimeError:
            with self._rebuild_lock:
                self._active_rebuilds.pop(root, None)
            done_event.set()

    def _rebuild_worker(self, root: Path, task: _RebuildTask) -> None:
        try:
            if task.cancel_event.is_set():  # cancelled before work began
                with self._rebuild_lock:
                    self._active_rebuilds.pop(root, None)
                return

            with self._rebuild_lock:  # bail if another root took ownership
                if self._target_root != root:
                    self._active_rebuilds.pop(root, None)
                    return

            with self._lock:  # exclusive access while rebuilding the store
                if task.cancel_event.is_set():
                    with self._rebuild_lock:
                        self._active_rebuilds.pop(root, None)
                    return

                self._store.rebuild(
                    root, should_cancel=lambda: task.cancel_event.is_set()
                )

            with self._rebuild_lock:
                self._active_rebuilds.pop(root, None)
        except Exception:
            with self._rebuild_lock:
                self._active_rebuilds.pop(root, None)
        finally:
            task.done_event.set()

    def _wait_for_rebuild(self, root: Path) -> None:
        with self._rebuild_lock:
            task = self._active_rebuilds.get(root)
        if task:
            task.done_event.wait()

    def _handle_watch_changes(
        self, root: Path, raw_changes: Iterable[tuple[Change, str]]
    ) -> None:
        normalized: list[tuple[Change, Path]] = []
        for change, path_str in raw_changes:
            if change not in {Change.added, Change.deleted, Change.modified}:
                continue
            normalized.append((change, Path(path_str).resolve()))

        if not normalized:
            return

        with self._lock:  # make watcher ignore stale roots
            if self._store.root != root:
                return
            self._store.apply_changes(normalized)
