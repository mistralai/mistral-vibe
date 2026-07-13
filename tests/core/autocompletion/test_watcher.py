from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from vibe.core.autocompletion.file_indexer.watcher import WatchController


class TestWatchControllerIsWatching:
    @pytest.fixture
    def watch_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "watch"

    @pytest.fixture
    def watcher(self) -> Generator[WatchController, None, None]:
        changes: list = []
        controller = WatchController(on_changes=lambda root, c: changes.extend(c))
        yield controller
        controller.stop()

    def test_is_watching_false_initially(self, watcher: WatchController) -> None:
        assert watcher.is_watching is False

    def test_is_watching_true_after_start(
        self, watcher: WatchController, watch_dir: Path
    ) -> None:
        watch_dir.mkdir()
        watcher.start(watch_dir)
        assert watcher.is_watching is True

    def test_is_watching_false_after_stop(
        self, watcher: WatchController, watch_dir: Path
    ) -> None:
        watch_dir.mkdir()
        watcher.start(watch_dir)
        assert watcher.is_watching is True
        watcher.stop()
        assert watcher.is_watching is False

    def test_is_watching_true_after_restart(
        self, watcher: WatchController, watch_dir: Path
    ) -> None:
        watch_dir.mkdir()
        watcher.start(watch_dir)
        watcher.stop()
        assert watcher.is_watching is False
        watcher.start(watch_dir)
        assert watcher.is_watching is True
