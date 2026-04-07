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


@pytest.mark.asyncio
async def test_searches_in_subdirectory(glob_tool, tmp_path):
    subdir = tmp_path / "src"
    subdir.mkdir()
    (subdir / "app.py").write_text("pass\n")
    (tmp_path / "root.py").write_text("pass\n")

    result = await collect_result(
        glob_tool.run(GlobArgs(pattern="**/*.py", path="src"))
    )

    assert result.total_count == 1
    assert any("app.py" in f for f in result.files)
    assert not any("root.py" in f for f in result.files)


@pytest.mark.asyncio
async def test_returns_empty_on_no_matches(glob_tool, tmp_path):
    (tmp_path / "file.txt").write_text("hello\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*.py")))

    assert result.total_count == 0
    assert result.files == []
    assert not result.was_truncated


@pytest.mark.asyncio
async def test_raises_error_for_nonexistent_path(glob_tool):
    with pytest.raises(ToolError) as err:
        await collect_result(glob_tool.run(GlobArgs(pattern="*", path="nonexistent")))

    assert "Path does not exist" in str(err.value)


@pytest.mark.asyncio
async def test_raises_error_for_file_path(glob_tool, tmp_path):
    (tmp_path / "file.txt").write_text("hello\n")

    with pytest.raises(ToolError) as err:
        await collect_result(glob_tool.run(GlobArgs(pattern="*", path="file.txt")))

    assert "not a directory" in str(err.value)


@pytest.mark.asyncio
async def test_truncates_to_max_results(glob_tool, tmp_path):
    for i in range(50):
        (tmp_path / f"file_{i:03d}.py").write_text("pass\n")

    result = await collect_result(
        glob_tool.run(GlobArgs(pattern="**/*.py", max_results=10))
    )

    assert len(result.files) == 10
    assert result.total_count == 50
    assert result.was_truncated


@pytest.mark.asyncio
async def test_excludes_git_directory(glob_tool, tmp_path):
    git_dir = tmp_path / ".git" / "objects"
    git_dir.mkdir(parents=True)
    (git_dir / "abc123").write_text("blob\n")
    (tmp_path / "main.py").write_text("pass\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*")))

    assert any("main.py" in f for f in result.files)
    assert not any(".git" in f for f in result.files)


@pytest.mark.asyncio
async def test_excludes_node_modules(glob_tool, tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {}\n")
    (tmp_path / "app.js").write_text("console.log('hi')\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*.js")))

    assert any("app.js" in f for f in result.files)
    assert not any("node_modules" in f for f in result.files)


@pytest.mark.asyncio
async def test_excludes_pycache(glob_tool, tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-312.pyc").write_text("")
    (tmp_path / "module.py").write_text("pass\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*")))

    assert any("module.py" in f for f in result.files)
    assert not any("__pycache__" in f for f in result.files)


@pytest.mark.asyncio
async def test_respects_vibeignore(glob_tool, tmp_path):
    (tmp_path / ".vibeignore").write_text("custom_dir/\n*.tmp\n")
    custom = tmp_path / "custom_dir"
    custom.mkdir()
    (custom / "excluded.py").write_text("pass\n")
    (tmp_path / "excluded.tmp").write_text("data\n")
    (tmp_path / "included.py").write_text("pass\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*")))

    assert any("included.py" in f for f in result.files)
    assert not any("excluded.py" in f for f in result.files)
    assert not any("excluded.tmp" in f for f in result.files)


@pytest.mark.asyncio
async def test_results_are_sorted(glob_tool, tmp_path):
    for name in ["zebra.py", "alpha.py", "middle.py"]:
        (tmp_path / name).write_text("pass\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*.py")))

    assert result.files == sorted(result.files)


@pytest.mark.asyncio
async def test_returns_only_files_not_directories(glob_tool, tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file.py").write_text("pass\n")

    result = await collect_result(glob_tool.run(GlobArgs(pattern="**/*")))

    for f in result.files:
        assert "subdir" != f
