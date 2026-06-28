from __future__ import annotations

import os

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.glob import Glob, GlobArgs, GlobToolConfig


@pytest.fixture
def glob_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = GlobToolConfig()
    return Glob(config_getter=lambda: config, state=BaseToolState())


@pytest.mark.asyncio
async def test_matches_recursive_pattern(glob_tool, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2\n")
    (tmp_path / "sub" / "c.txt").write_text("nope\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*.py")))

    assert result.match_count == 2
    assert any(m.endswith("a.py") for m in result.matches)
    assert any(m.endswith("b.py") for m in result.matches)
    assert not any(m.endswith("c.txt") for m in result.matches)


@pytest.mark.asyncio
async def test_sorts_newest_first(glob_tool, tmp_path):
    old = tmp_path / "old.py"
    new = tmp_path / "new.py"
    old.write_text("1\n")
    new.write_text("2\n")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    result = await collect_result(glob_tool.run(GlobArgs(pattern="*.py")))

    assert result.matches[0].endswith("new.py")
    assert result.matches[1].endswith("old.py")


@pytest.mark.asyncio
async def test_prunes_excluded_dirs(glob_tool, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("secret\n")
    (tmp_path / "keep.py").write_text("ok\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*.py")))

    assert result.match_count == 1
    assert result.matches[0].endswith("keep.py")


@pytest.mark.asyncio
async def test_truncates_to_max_results(glob_tool, tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("1\n")

    result = await collect_result(
        glob_tool.run(GlobArgs(pattern="*.py", max_results=2))
    )

    assert result.match_count == 2
    assert result.was_truncated is True


@pytest.mark.asyncio
async def test_empty_pattern_raises(glob_tool):
    with pytest.raises(ToolError):
        await collect_result(glob_tool.run(GlobArgs(pattern="   ")))


@pytest.mark.asyncio
async def test_missing_path_raises(glob_tool):
    with pytest.raises(ToolError):
        await collect_result(
            glob_tool.run(GlobArgs(pattern="*.py", path="does_not_exist"))
        )


def test_tool_name_is_glob():
    assert Glob.get_name() == "glob"
