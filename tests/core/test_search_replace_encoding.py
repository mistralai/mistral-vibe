from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
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


@pytest.mark.asyncio
async def test_search_replace_errors_on_ambiguous_search_with_suggestions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "ambiguous.txt"
    path.write_text("alpha\ntarget\nomega\nbeta\ntarget\ntheta\n", encoding="utf-8")

    tool = SearchReplace(
        config_getter=lambda: SearchReplaceConfig(), state=BaseToolState()
    )
    patch = "<<<<<<< SEARCH\ntarget\n=======\nTARGET\n>>>>>>> REPLACE"

    with pytest.raises(ToolError) as exc_info:
        await collect_result(
            tool.run(SearchReplaceArgs(file_path=str(path), content=patch))
        )

    message = str(exc_info.value)
    assert "Search text is ambiguous" in message
    assert "It appears 2 times" in message
    assert "The tool will not guess" in message
    assert "Expanded source regions:" in message
    assert "Source region lines 1-3:\n```\nalpha\ntarget\nomega\n```" in message
    assert "Source region lines 4-6:\n```\nbeta\ntarget\ntheta\n```" in message
    assert "<<<<<<< SEARCH" not in message
    assert "TARGET" not in message
    assert path.read_text(encoding="utf-8") == (
        "alpha\ntarget\nomega\nbeta\ntarget\ntheta\n"
    )


@pytest.mark.asyncio
async def test_search_replace_errors_on_many_ambiguous_matches_without_suggestions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "many.txt"
    path.write_text("target\n" * 4, encoding="utf-8")

    tool = SearchReplace(
        config_getter=lambda: SearchReplaceConfig(), state=BaseToolState()
    )
    patch = "<<<<<<< SEARCH\ntarget\n=======\nTARGET\n>>>>>>> REPLACE"

    with pytest.raises(ToolError) as exc_info:
        await collect_result(
            tool.run(SearchReplaceArgs(file_path=str(path), content=patch))
        )

    message = str(exc_info.value)
    assert "Search text is ambiguous" in message
    assert "It appears 4 times" in message
    assert "<<<<<<< SEARCH" not in message
    assert path.read_text(encoding="utf-8") == "target\n" * 4


@pytest.mark.asyncio
async def test_search_replace_merges_overlapping_ambiguous_source_regions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "adjacent.txt"
    path.write_text("A\ntarget\ntarget\nB\n", encoding="utf-8")

    tool = SearchReplace(
        config_getter=lambda: SearchReplaceConfig(), state=BaseToolState()
    )
    patch = "<<<<<<< SEARCH\ntarget\n=======\nTARGET\n>>>>>>> REPLACE"

    with pytest.raises(ToolError) as exc_info:
        await collect_result(
            tool.run(SearchReplaceArgs(file_path=str(path), content=patch))
        )

    message = str(exc_info.value)
    assert "Expanded source region:" in message
    assert "Expanded source regions:" not in message
    assert "Source region lines 1-4:\n```\nA\ntarget\ntarget\nB\n```" in message
    assert message.count("Source region lines") == 1
    assert "<<<<<<< SEARCH" not in message
    assert path.read_text(encoding="utf-8") == "A\ntarget\ntarget\nB\n"
