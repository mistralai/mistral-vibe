from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Event, Thread

from watchfiles import Change, watch


class WatchController:
    def __init__(
        self, on_changes: Callable[[Path, Iterable[tuple[Change, str]]], None]
    ) -> None:
        self._on_changes = on_changes
        self._thread: Thread | None = None
        self._stop_event: Event | None = None
        self._ready_event: Event | None = None
        self._root: Path | None = None

    def start(self, root: Path) -> None:
        resolved_root = root.resolve()
        if self._thread and self._thread.is_alive() and self._root == resolved_root:
            return

        self.stop()

        stop_event = Event()
        ready_event = Event()
        thread = Thread(
            target=self._watch_loop,
            args=(resolved_root, stop_event, ready_event),
            name="file-indexer-watch",
            daemon=True,
        )

        self._thread = thread
        self._stop_event = stop_event
        self._ready_event = ready_event
        self._root = resolved_root

        thread.start()
        ready_event.wait(timeout=0.5)

    def stop(self) -> None:
        thread = self._thread
        if self._stop_event:
            self._stop_event.set()
        self._thread = None
        self._stop_event = None
        self._ready_event = None
        self._root = None

        if thread and thread.is_alive():
            thread.join(timeout=1)

    def _watch_loop(self, root: Path, stop_event: Event, ready_event: Event) -> None:
        try:
            watcher = watch(
                str(root), stop_event=stop_event, step=200, yield_on_timeout=True
            )
            ready_event.set()
            for changes in watcher:
                if not ready_event.is_set():
                    ready_event.set()
                if stop_event.is_set():
                    break
                if not changes:
                    continue
                self._on_changes(root, changes)
        except Exception:
            ready_event.set()
