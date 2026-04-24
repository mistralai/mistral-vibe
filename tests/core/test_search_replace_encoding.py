from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState
from vibe.core.tools.builtins.search_replace import (
    SearchReplace,
    SearchReplaceArgs,
    SearchReplaceConfig,
)


@pytest.mark.asyncio
async def test_search_replace_rewrites_with_detected_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "utf16.txt"
    original = "line one café\nline two été\n"
    path.write_bytes(original.encode("utf-16"))

    tool = SearchReplace(
        config_getter=lambda: SearchReplaceConfig(), state=BaseToolState()
    )
    patch = "<<<<<<< SEARCH\nline one café\n=======\nLINE ONE CAFÉ\n>>>>>>> REPLACE"
    await collect_result(
        tool.run(SearchReplaceArgs(file_path=str(path), content=patch))
    )

    assert path.read_bytes().startswith(b"\xff\xfe")
    assert path.read_text(encoding="utf-16") == "LINE ONE CAFÉ\nline two été\n"
