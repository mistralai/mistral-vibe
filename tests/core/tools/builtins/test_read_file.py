from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.tools.builtins.read_file import (
    ReadFile,
    ReadFileArgs,
    ReadFileResult,
    ReadFileState,
    ReadFileToolConfig,
)
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.utils import VIBE_WARNING_TAG


@pytest.fixture()
def _setup_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Initialize harness files manager for tests, reset after."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
    monkeypatch.setattr(
        trusted_folders_manager, "find_trust_root", lambda _: tmp_path.resolve()
    )
    reset_harness_files_manager()
    init_harness_files_manager("user", "project")
    yield
    reset_harness_files_manager()


def _make_read_file() -> ReadFile:
    return ReadFile(config_getter=lambda: ReadFileToolConfig(), state=ReadFileState())


class TestReadFileExecution:
    @pytest.mark.asyncio
    async def test_run_with_large_offset_still_reads_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "large_file.txt"
        test_file.write_text(
            "".join(f"line {i}\n" for i in range(200)), encoding="utf-8"
        )
        tool = ReadFile(
            config_getter=lambda: ReadFileToolConfig(max_read_bytes=64),
            state=ReadFileState(),
        )

        result = await collect_result(
            tool.run(ReadFileArgs(path=str(test_file), offset=50, limit=2))
        )

        assert result.content == "line 50\nline 51\n"
        assert result.lines_read == 2
        assert not result.was_truncated


class TestGetResultExtra:
    @pytest.mark.usefixtures("_setup_manager")
    def test_returns_none_when_no_agents_md(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        target = sub / "file.py"
        target.write_text("hello", encoding="utf-8")

        tool = _make_read_file()
        result = ReadFileResult(
            path=str(target), content="hello", lines_read=1, was_truncated=False
        )
        assert tool.get_result_extra(result) is None

    @pytest.mark.usefixtures("_setup_manager")
    def test_returns_tagged_content_when_agents_md_found(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("# Sub instructions", encoding="utf-8")
        target = sub / "file.py"
        target.write_text("hello", encoding="utf-8")

        tool = _make_read_file()
        result = ReadFileResult(
            path=str(target), content="hello", lines_read=1, was_truncated=False
        )
        annotation = tool.get_result_extra(result)
        assert annotation is not None
        assert f"<{VIBE_WARNING_TAG}>" in annotation
        assert f"</{VIBE_WARNING_TAG}>" in annotation
        assert "# Sub instructions" in annotation
        assert "project instructions for this directory" in annotation

    @pytest.mark.usefixtures("_setup_manager")
    def test_deduplicates_across_calls(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("# Sub", encoding="utf-8")
        file1 = sub / "a.py"
        file2 = sub / "b.py"
        file1.write_text("a", encoding="utf-8")
        file2.write_text("b", encoding="utf-8")

        tool = _make_read_file()

        result1 = ReadFileResult(
            path=str(file1), content="a", lines_read=1, was_truncated=False
        )
        assert tool.get_result_extra(result1) is not None

        # Second call for a different file in the same dir → no duplicate
        result2 = ReadFileResult(
            path=str(file2), content="b", lines_read=1, was_truncated=False
        )
        assert tool.get_result_extra(result2) is None

    @pytest.mark.usefixtures("_setup_manager")
    def test_injects_new_dir_after_dedup(self, tmp_path: Path) -> None:
        sub_a = tmp_path / "a"
        sub_b = tmp_path / "b"
        sub_a.mkdir()
        sub_b.mkdir()
        (sub_a / "AGENTS.md").write_text("# A", encoding="utf-8")
        (sub_b / "AGENTS.md").write_text("# B", encoding="utf-8")
        file_a = sub_a / "f.py"
        file_b = sub_b / "f.py"
        file_a.write_text("", encoding="utf-8")
        file_b.write_text("", encoding="utf-8")

        tool = _make_read_file()

        r1 = ReadFileResult(
            path=str(file_a), content="", lines_read=0, was_truncated=False
        )
        ann1 = tool.get_result_extra(r1)
        assert ann1 is not None
        assert "# A" in ann1

        # Different subdirectory → should inject its AGENTS.md
        r2 = ReadFileResult(
            path=str(file_b), content="", lines_read=0, was_truncated=False
        )
        ann2 = tool.get_result_extra(r2)
        assert ann2 is not None
        assert "# B" in ann2

    def test_returns_none_when_manager_not_initialized(self, tmp_path: Path) -> None:
        reset_harness_files_manager()
        tool = _make_read_file()
        result = ReadFileResult(
            path=str(tmp_path / "file.py"),
            content="",
            lines_read=0,
            was_truncated=False,
        )
        assert tool.get_result_extra(result) is None
        reset_harness_files_manager()
