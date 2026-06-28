from __future__ import annotations

import shutil

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState
from vibe.core.tools.builtins.grep import Grep, GrepArgs, GrepOutputMode, GrepToolConfig

requires_grep = pytest.mark.skipif(
    shutil.which("rg") is None and shutil.which("grep") is None,
    reason="neither ripgrep nor grep installed",
)


@pytest.fixture
def grep(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = GrepToolConfig()
    return Grep(config_getter=lambda: config, state=BaseToolState())


def _seed(tmp_path):
    (tmp_path / "a.py").write_text("needle here\nother line\nneedle again\n")
    (tmp_path / "b.txt").write_text("needle in text\n")


@pytest.mark.asyncio
@requires_grep
async def test_files_with_matches_mode_lists_paths(grep, tmp_path):
    _seed(tmp_path)

    result = await collect_result(
        grep.run(
            GrepArgs(pattern="needle", output_mode=GrepOutputMode.FILES_WITH_MATCHES)
        )
    )

    assert "a.py" in result.matches
    assert "b.txt" in result.matches
    assert "needle here" not in result.matches


@pytest.mark.asyncio
@requires_grep
async def test_count_mode_reports_per_file_counts(grep, tmp_path):
    _seed(tmp_path)

    result = await collect_result(
        grep.run(GrepArgs(pattern="needle", output_mode=GrepOutputMode.COUNT))
    )

    assert "a.py:2" in result.matches


@pytest.mark.asyncio
@requires_grep
async def test_glob_filter_restricts_files(grep, tmp_path):
    _seed(tmp_path)

    result = await collect_result(grep.run(GrepArgs(pattern="needle", glob="*.py")))

    assert "a.py" in result.matches
    assert "b.txt" not in result.matches


@pytest.mark.asyncio
@requires_grep
async def test_context_after_includes_following_lines(grep, tmp_path):
    (tmp_path / "c.py").write_text("start\nMATCH\ntrailing\n")

    result = await collect_result(grep.run(GrepArgs(pattern="MATCH", context_after=1)))

    assert "MATCH" in result.matches
    assert "trailing" in result.matches


@pytest.mark.asyncio
@requires_grep
async def test_ignore_case_matches_mixed_case(grep, tmp_path):
    (tmp_path / "d.py").write_text("HELLO World\n")

    result = await collect_result(grep.run(GrepArgs(pattern="hello", ignore_case=True)))

    assert result.match_count == 1


def test_ripgrep_command_uses_files_with_matches_flag(grep):
    cmd = grep._build_ripgrep_command(
        GrepArgs(pattern="x", output_mode=GrepOutputMode.FILES_WITH_MATCHES), []
    )
    assert "--files-with-matches" in cmd
    assert "--line-number" not in cmd


def test_ripgrep_command_adds_context_flags(grep):
    cmd = grep._build_ripgrep_command(
        GrepArgs(pattern="x", context_before=2, context_after=3), []
    )
    assert "-B" in cmd and "2" in cmd
    assert "-A" in cmd and "3" in cmd
