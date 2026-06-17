from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.edit import Edit, EditArgs, EditConfig, EditResult
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay
from vibe.core.types import ToolResultEvent


def _make_edit() -> Edit:
    return Edit(config_getter=lambda: EditConfig(), state=BaseToolState())


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


@pytest.mark.asyncio
async def test_exact_match_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "hello world\n")
    edit = _make_edit()

    result = await collect_result(
        edit.run(EditArgs(file_path="f.txt", old_string="hello", new_string="goodbye"))
    )

    assert (tmp_path / "f.txt").read_text() == "goodbye world\n"
    assert result.file == str(tmp_path / "f.txt")
    assert result.message == "The file has been updated successfully."


@pytest.mark.asyncio
async def test_replace_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "aaa bbb aaa\n")
    edit = _make_edit()

    result = await collect_result(
        edit.run(
            EditArgs(
                file_path="f.txt", old_string="aaa", new_string="ccc", replace_all=True
            )
        )
    )

    assert (tmp_path / "f.txt").read_text() == "ccc bbb ccc\n"
    assert result.message == (
        "The file has been updated. All occurrences were successfully replaced"
    )


@pytest.mark.asyncio
async def test_not_found_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "hello world\n")
    edit = _make_edit()

    with pytest.raises(ToolError, match="String to replace not found in file"):
        await collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="missing", new_string="x"))
        )


@pytest.mark.asyncio
async def test_multiple_matches_without_replace_all_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "aaa bbb aaa\n")
    edit = _make_edit()

    with pytest.raises(ToolError, match="Found 2 matches"):
        await collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="aaa", new_string="ccc"))
        )


@pytest.mark.asyncio
async def test_old_equals_new_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "hello\n")
    edit = _make_edit()

    with pytest.raises(ToolError, match="No changes to make"):
        await collect_result(
            edit.run(
                EditArgs(file_path="f.txt", old_string="hello", new_string="hello")
            )
        )


@pytest.mark.asyncio
async def test_empty_old_string_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "hello\n")
    edit = _make_edit()

    with pytest.raises(ToolError, match="old_string cannot be empty"):
        await collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="", new_string="x"))
        )


@pytest.mark.asyncio
async def test_file_not_found_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    edit = _make_edit()

    with pytest.raises(ToolError, match="File does not exist"):
        await collect_result(
            edit.run(
                EditArgs(
                    file_path="/nonexistent/file.py", old_string="x", new_string="y"
                )
            )
        )


@pytest.mark.asyncio
async def test_empty_file_path_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    edit = _make_edit()

    with pytest.raises(ToolError, match="File path cannot be empty"):
        await collect_result(
            edit.run(EditArgs(file_path="", old_string="x", new_string="y"))
        )


@pytest.mark.asyncio
async def test_deletion_removes_exact_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "line1\nline2\nline3\n")
    edit = _make_edit()

    await collect_result(
        edit.run(EditArgs(file_path="f.txt", old_string="line2\n", new_string=""))
    )

    assert (tmp_path / "f.txt").read_text() == "line1\nline3\n"


@pytest.mark.asyncio
async def test_parallel_edits_same_file_all_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "A\nB\nC\nD\n")
    edit = _make_edit()

    await asyncio.gather(
        collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="A", new_string="A1"))
        ),
        collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="B", new_string="B1"))
        ),
        collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="C", new_string="C1"))
        ),
        collect_result(
            edit.run(EditArgs(file_path="f.txt", old_string="D", new_string="D1"))
        ),
    )

    assert (tmp_path / "f.txt").read_text() == "A1\nB1\nC1\nD1\n"


@pytest.mark.asyncio
async def test_relative_path_resolved_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir(exist_ok=True)
    _write(tmp_path / "sub", "f.txt", "old")
    edit = _make_edit()

    result = await collect_result(
        edit.run(EditArgs(file_path="sub/f.txt", old_string="old", new_string="new"))
    )

    assert (tmp_path / "sub" / "f.txt").read_text() == "new"
    assert result.file == str(tmp_path / "sub" / "f.txt")


def test_format_call_display() -> None:
    args = EditArgs(file_path="/abs/foo.py", old_string="old", new_string="new")
    display = Edit.format_call_display(args)

    assert isinstance(display, ToolCallDisplay)
    assert display.summary == "Editing foo.py"


def test_get_result_display() -> None:
    result = EditResult(
        file="/path/to/foo.py",
        message="The file has been updated successfully.",
        old_string="old",
        new_string="new",
    )
    event = ToolResultEvent(
        tool_call_id="test", tool_name="edit", tool_class=None, result=result
    )
    display = Edit.get_result_display(event)

    assert isinstance(display, ToolResultDisplay)
    assert display.success is True
    assert "foo.py" in display.message


def test_ui_start_line_not_part_of_model_contract() -> None:
    result = EditResult(file="/x", message="m", old_string="a", new_string="b")
    result._ui_start_line = 42

    assert result.ui_start_line == 42
    assert "ui_start_line" not in result.model_dump()
    assert "_ui_start_line" not in result.model_dump()
    assert "ui_start_line" not in result.model_dump_json()
    assert "ui_start_line" not in EditResult.model_fields
    assert "ui_start_line" not in EditResult.model_json_schema().get("properties", {})
    assert "ui_start_line" not in dict(result)


@pytest.mark.asyncio
async def test_ui_start_line_computed_at_edit_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "alpha\nbeta\ngamma\ndelta\n")
    edit = _make_edit()

    result = await collect_result(
        edit.run(EditArgs(file_path="f.txt", old_string="gamma", new_string="GAMMA"))
    )

    assert result.ui_start_line == 3


@pytest.mark.asyncio
async def test_ui_start_line_set_for_pure_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, "f.txt", "keep1\nkeep2\nremove\nkeep3\n")
    edit = _make_edit()

    result = await collect_result(
        edit.run(EditArgs(file_path="f.txt", old_string="remove\n", new_string=""))
    )

    assert result.ui_start_line == 3
