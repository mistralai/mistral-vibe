from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.edit import Edit, EditArgs, EditConfig


@pytest.mark.asyncio
async def test_edit_rewrites_with_detected_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "utf16.txt"
    original = "line one café\nline two été\n"
    path.write_bytes(original.encode("utf-16"))

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    await collect_result(
        tool.run(
            EditArgs(
                file_path=str(path),
                old_string="line one café",
                new_string="LINE ONE CAFÉ",
            )
        )
    )

    assert path.read_bytes().startswith(b"\xff\xfe")
    assert path.read_text(encoding="utf-16") == "LINE ONE CAFÉ\nline two été\n"


@pytest.mark.asyncio
async def test_edit_preserves_file_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "script.sh"
    path.write_text("#!/bin/sh\necho old\n")
    os.chmod(path, 0o755)

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    await collect_result(
        tool.run(EditArgs(file_path=str(path), old_string="old", new_string="new"))
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o755


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
async def test_edit_preserves_line_endings(
    newline: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "f.txt"
    original = newline.join(["alpha", "beta", "gamma"])
    path.write_bytes(original.encode("utf-8"))

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    await collect_result(
        tool.run(EditArgs(file_path=str(path), old_string="beta", new_string="BETA"))
    )

    assert path.read_bytes() == newline.join(["alpha", "BETA", "gamma"]).encode("utf-8")


@pytest.mark.asyncio
async def test_edit_binary_file_raises_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "blob.bin"
    # PNG header + some non-text payload.
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4)

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    with pytest.raises(ToolError, match="not valid text"):
        await collect_result(
            tool.run(EditArgs(file_path=str(path), old_string="PNG", new_string="JPG"))
        )
