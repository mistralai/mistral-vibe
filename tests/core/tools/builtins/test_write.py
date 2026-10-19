from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.tools.base import BaseToolState
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
    WriteFileResult,
)
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import ToolResultEvent


@pytest.fixture()
def _setup_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
    monkeypatch.setattr(
        trusted_folders_manager, "find_trust_root", lambda _: tmp_path.resolve()
    )
    reset_harness_files_manager()
    init_harness_files_manager("user", "project")
    yield
    reset_harness_files_manager()


@pytest.mark.usefixtures("_setup_manager")
def test_format_call_display_relative_to_cwd(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "config.py"

    args = WriteFileArgs(file_path=str(target), content="x")
    display = WriteFile.format_call_display(args)

    assert display.summary == "Writing pkg/config.py"
    assert display.message == "pkg/config.py"
    assert display.settled_message == "pkg/config.py"


@pytest.mark.usefixtures("_setup_manager")
def test_get_result_display_relative_to_cwd(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "config.py"

    result = WriteFileResult(file_path=str(target), bytes_written=1, content="x")
    event = ToolResultEvent(
        tool_call_id="test", tool_name="write_file", tool_class=None, result=result
    )
    display = WriteFile.get_result_display(event)

    assert display.success is True
    assert display.message == "pkg/config.py"
    assert display.message != "config.py"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["a\r\nb\r\n", "a\nb\n"])
async def test_writes_content_line_endings_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "new.txt"
    tool = WriteFile(config_getter=lambda: WriteFileConfig(), state=BaseToolState())

    await collect_result(
        tool.run(WriteFileArgs(file_path=str(target), content=content))
    )

    assert target.read_bytes() == content.encode("utf-8")
