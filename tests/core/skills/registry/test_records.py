from __future__ import annotations

import os
from pathlib import Path

import pytest

from vibe.core.skills.registry import _records


def test_a_record_reaches_the_disk_before_it_replaces_the_old_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps: list[str] = []
    fsync, replace = os.fsync, os.replace

    def synced(descriptor: int) -> None:
        steps.append("fsync")
        fsync(descriptor)

    def renamed(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        steps.append("rename")
        replace(source, target)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(os, "replace", renamed)
    path = tmp_path / "record.json"

    _records.replace(path, {"cid@stable": 3})

    assert steps == ["fsync", "rename"]
    assert _records.read(path) == {"cid@stable": 3}
