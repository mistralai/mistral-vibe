from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.glob import Glob, GlobArgs, GlobToolConfig


@pytest.fixture
def glob_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = GlobToolConfig()
    return Glob(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_finds_files_by_pattern(glob_tool, tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    (tmp_path / "utils.py").write_text("pass\n")
    (tmp_path / "readme.md").write_text("# Readme\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*.py")))

    assert result.total_count == 2
    assert any("main.py" in f for f in result.files)
    assert any("utils.py" in f for f in result.files)
    assert not any("readme.md" in f for f in result.files)
    assert not result.was_truncated
