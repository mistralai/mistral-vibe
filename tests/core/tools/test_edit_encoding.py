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
@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
@pytest.mark.parametrize("block_newline", ["\n", "\r\n"])
async def test_multi_line_edit_matches_either_line_ending_and_keeps_the_file_one(
    newline: str, block_newline: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "f.txt"
    path.write_bytes(newline.join(["alpha", "beta", "gamma", ""]).encode("utf-8"))

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    result = await collect_result(
        tool.run(
            EditArgs(
                file_path=str(path),
                old_string=block_newline.join(["alpha", "beta"]),
                new_string=block_newline.join(["ALPHA", "inserted", "BETA"]),
            )
        )
    )

    assert path.read_bytes() == newline.join([
        "ALPHA",
        "inserted",
        "BETA",
        "gamma",
        "",
    ]).encode("utf-8")
    assert result.old_string == "alpha\nbeta"
    assert result.new_string == "ALPHA\ninserted\nBETA"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old_string", "new_string"),
    [
        ("beta\r", "BETA"),
        ("beta", "BETA\r"),
        ("beta\r", "BETA\r"),
        ("beta\r\n", "BETA\r"),
        ("beta\r", "BETA\r\n"),
    ],
)
async def test_edit_ignores_a_trailing_cr_copied_from_a_crlf_line(
    old_string: str, new_string: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "f.txt"
    path.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    await collect_result(
        tool.run(
            EditArgs(file_path=str(path), old_string=old_string, new_string=new_string)
        )
    )

    assert path.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


@pytest.mark.asyncio
async def test_a_trailing_cr_only_matches_at_the_end_of_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "f.txt"
    path.write_bytes(b"foo\r\nfoobar\r\n")

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    await collect_result(
        tool.run(
            EditArgs(
                file_path=str(path),
                old_string="foo\r",
                new_string="baz\r",
                replace_all=True,
            )
        )
    )

    assert path.read_bytes() == b"baz\r\nfoobar\r\n"


@pytest.mark.asyncio
async def test_edit_rejects_an_old_string_that_is_only_a_cr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "f.txt"
    path.write_bytes(b"alpha\r\nbeta\r\n")

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    with pytest.raises(ToolError, match="cannot be empty"):
        await collect_result(
            tool.run(EditArgs(file_path=str(path), old_string="\r", new_string="x"))
        )


@pytest.mark.asyncio
async def test_edit_rejects_strings_that_only_differ_by_line_endings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "f.txt"
    path.write_bytes(b"alpha\r\nbeta\r\n")

    tool = Edit(config_getter=lambda: EditConfig(), state=BaseToolState())
    with pytest.raises(ToolError, match="identical"):
        await collect_result(
            tool.run(
                EditArgs(
                    file_path=str(path),
                    old_string="alpha\r\nbeta",
                    new_string="alpha\nbeta",
                )
            )
        )


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
