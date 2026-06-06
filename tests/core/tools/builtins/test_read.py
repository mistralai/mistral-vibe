from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.read import (
    DEFAULT_LINE_LIMIT,
    MAX_BYTES,
    Read,
    ReadArgs,
    ReadConfig,
    ReadResult,
    ReadState,
    _add_line_numbers,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import ToolResultEvent
from vibe.core.utils import VIBE_WARNING_TAG


def _make_read() -> Read:
    return Read(config_getter=lambda: ReadConfig(), state=ReadState())


@pytest.mark.asyncio
async def test_reads_entire_small_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.txt").write_text("line one\nline two\n", encoding="utf-8")
    tool = _make_read()

    result = await collect_result(
        tool.run(ReadArgs(file_path=str(tmp_path / "hello.txt")))
    )

    assert result.num_lines == 2
    assert result.total_lines == 2
    assert result.start_line == 1
    assert "line one" in result.content
    assert "line two" in result.content


@pytest.mark.asyncio
async def test_reads_with_offset_and_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    content = "".join(f"line {i}\n" for i in range(1, 11))
    (tmp_path / "f.txt").write_text(content, encoding="utf-8")
    tool = _make_read()

    result = await collect_result(
        tool.run(ReadArgs(file_path=str(tmp_path / "f.txt"), offset=3, limit=2))
    )

    assert result.num_lines == 2
    assert result.start_line == 3
    # Bounded read stops at the limit, so the true total is unknown.
    assert result.total_lines is None
    assert result.was_truncated is True
    assert "line 3" in result.content
    assert "line 4" in result.content
    assert "line 2" not in result.content
    assert "line 5" not in result.content


@pytest.mark.asyncio
async def test_empty_file_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    tool = _make_read()

    result = await collect_result(
        tool.run(ReadArgs(file_path=str(tmp_path / "empty.txt")))
    )

    assert result.num_lines == 0
    assert VIBE_WARNING_TAG in result.content
    assert "empty" in result.content


@pytest.mark.asyncio
async def test_offset_beyond_file_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "short.txt").write_text("one\ntwo\n", encoding="utf-8")
    tool = _make_read()

    result = await collect_result(
        tool.run(ReadArgs(file_path=str(tmp_path / "short.txt"), offset=100))
    )

    assert result.num_lines == 0
    assert VIBE_WARNING_TAG in result.content
    assert "shorter" in result.content


@pytest.mark.asyncio
async def test_exceeds_max_bytes_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # Create file that generates output exceeding MAX_BYTES
    big_line = "x" * 200 + "\n"
    lines_needed = (MAX_BYTES // len(big_line)) + 100
    (tmp_path / "big.txt").write_text(big_line * lines_needed, encoding="utf-8")
    tool = _make_read()

    with pytest.raises(ToolError, match="exceeds maximum allowed size"):
        await collect_result(tool.run(ReadArgs(file_path=str(tmp_path / "big.txt"))))


@pytest.mark.asyncio
async def test_truncated_when_more_lines_than_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    content = "".join(f"line {i}\n" for i in range(1, 101))
    (tmp_path / "f.txt").write_text(content, encoding="utf-8")
    tool = _make_read()

    result = await collect_result(
        tool.run(ReadArgs(file_path=str(tmp_path / "f.txt"), limit=10))
    )

    assert result.num_lines == 10
    assert result.was_truncated is True
    assert result.total_lines is None


@pytest.mark.asyncio
async def test_single_oversized_line_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wide.txt").write_text("x" * (MAX_BYTES + 10), encoding="utf-8")
    tool = _make_read()

    with pytest.raises(ToolError, match="exceeds maximum allowed size"):
        await collect_result(tool.run(ReadArgs(file_path=str(tmp_path / "wide.txt"))))


@pytest.mark.asyncio
async def test_file_not_found_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool = _make_read()

    with pytest.raises(ToolError, match="File not found"):
        await collect_result(tool.run(ReadArgs(file_path=str(tmp_path / "nope.txt"))))


@pytest.mark.asyncio
async def test_empty_path_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool = _make_read()

    with pytest.raises(ToolError, match="file_path cannot be empty"):
        await collect_result(tool.run(ReadArgs(file_path="")))


@pytest.mark.asyncio
async def test_directory_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "adir").mkdir()
    tool = _make_read()

    with pytest.raises(ToolError, match="directory"):
        await collect_result(tool.run(ReadArgs(file_path=str(tmp_path / "adir"))))


@pytest.mark.asyncio
async def test_relative_path_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("ok\n", encoding="utf-8")
    tool = _make_read()

    result = await collect_result(tool.run(ReadArgs(file_path="sub/f.txt")))

    assert result.num_lines == 1
    assert str(tmp_path / "sub" / "f.txt") == result.file_path


def test_line_number_format() -> None:
    formatted = _add_line_numbers(["hello", "world"], start=1)
    lines = formatted.split("\n")
    assert lines[0] == "        1\u2192hello"
    assert lines[1] == "        2\u2192world"


def test_default_limit_is_2000() -> None:
    assert DEFAULT_LINE_LIMIT == 2000


def test_format_call_display() -> None:
    args = ReadArgs(file_path="/some/file.py")
    display = Read.format_call_display(args)

    assert isinstance(display, ToolCallDisplay)
    assert "file.py" in display.summary


def test_format_call_display_with_offset_limit() -> None:
    args = ReadArgs(file_path="/some/file.py", offset=10, limit=50)
    display = Read.format_call_display(args)

    assert "from line 10" in display.summary
    assert "limit 50" in display.summary


def test_get_result_display() -> None:
    result = ReadResult(
        file_path="/path/to/foo.py",
        content="...",
        num_lines=10,
        start_line=1,
        total_lines=10,
    )
    event = ToolResultEvent(
        tool_call_id="test", tool_name="read", tool_class=None, result=result
    )
    display = Read.get_result_display(event)

    assert isinstance(display, ToolResultDisplay)
    assert display.success is True
    assert "10 lines" in display.message
    assert "foo.py" in display.message


def test_get_result_display_truncated() -> None:
    result = ReadResult(
        file_path="/path/to/foo.py",
        content="...",
        num_lines=10,
        start_line=1,
        total_lines=100,
    )
    event = ToolResultEvent(
        tool_call_id="test", tool_name="read", tool_class=None, result=result
    )
    display = Read.get_result_display(event)

    assert "truncated" in display.message


def test_get_result_display_truncated_via_flag() -> None:
    result = ReadResult(
        file_path="/path/to/foo.py",
        content="...",
        num_lines=10,
        start_line=1,
        total_lines=None,
        was_truncated=True,
    )
    event = ToolResultEvent(
        tool_call_id="test", tool_name="read", tool_class=None, result=result
    )
    display = Read.get_result_display(event)

    assert "truncated" in display.message


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
def test_agents_md_injection(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("# Sub instructions", encoding="utf-8")
    target = sub / "file.py"
    target.write_text("hello", encoding="utf-8")

    tool = _make_read()
    result = ReadResult(
        file_path=str(target), content="hello", num_lines=1, start_line=1, total_lines=1
    )
    annotation = tool.get_result_extra(result)
    assert annotation is not None
    assert VIBE_WARNING_TAG in annotation
    assert "# Sub instructions" in annotation


@pytest.mark.usefixtures("_setup_manager")
def test_agents_md_deduplicates(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("# Sub", encoding="utf-8")
    (sub / "a.py").write_text("a", encoding="utf-8")
    (sub / "b.py").write_text("b", encoding="utf-8")

    tool = _make_read()

    r1 = ReadResult(
        file_path=str(sub / "a.py"),
        content="a",
        num_lines=1,
        start_line=1,
        total_lines=1,
    )
    assert tool.get_result_extra(r1) is not None

    r2 = ReadResult(
        file_path=str(sub / "b.py"),
        content="b",
        num_lines=1,
        start_line=1,
        total_lines=1,
    )
    assert tool.get_result_extra(r2) is None


def test_agents_md_returns_none_when_not_initialized(tmp_path: Path) -> None:
    reset_harness_files_manager()
    tool = _make_read()
    result = ReadResult(
        file_path=str(tmp_path / "file.py"),
        content="",
        num_lines=0,
        start_line=1,
        total_lines=0,
    )
    assert tool.get_result_extra(result) is None
    reset_harness_files_manager()
