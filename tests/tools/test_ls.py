from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.ls import Ls, LsArgs, LsToolConfig


@pytest.fixture
def ls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = LsToolConfig()
    return Ls(config_getter=lambda: config, state=BaseToolState())


@pytest.mark.asyncio
async def test_lists_files_and_directories(ls, tmp_path):
    (tmp_path / "file.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()

    result = await collect_result(ls.run(LsArgs()))

    assert result.total_count == 2
    names = [e.name for e in result.entries]
    assert "file.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_lists_empty_directory(ls, tmp_path):
    result = await collect_result(ls.run(LsArgs()))

    assert result.total_count == 0
    assert result.entries == []


@pytest.mark.asyncio
async def test_raises_error_for_nonexistent_path(ls):
    with pytest.raises(ToolError, match="Path does not exist"):
        await collect_result(ls.run(LsArgs(path="nonexistent")))


@pytest.mark.asyncio
async def test_raises_error_for_file_path(ls, tmp_path):
    (tmp_path / "afile.txt").write_text("content")

    with pytest.raises(ToolError, match="Path is not a directory"):
        await collect_result(ls.run(LsArgs(path="afile.txt")))


@pytest.mark.asyncio
async def test_excludes_git_directory(ls, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()

    result = await collect_result(ls.run(LsArgs()))

    names = [e.name for e in result.entries]
    assert ".git" not in names
    assert "src" in names


@pytest.mark.asyncio
async def test_excludes_node_modules(ls, tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "index.js").write_text("console.log('hi')")

    result = await collect_result(ls.run(LsArgs()))

    names = [e.name for e in result.entries]
    assert "node_modules" not in names
    assert "index.js" in names


@pytest.mark.asyncio
async def test_directories_listed_first(ls, tmp_path):
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "beta_dir").mkdir()
    (tmp_path / "gamma.txt").write_text("g")
    (tmp_path / "delta_dir").mkdir()

    result = await collect_result(ls.run(LsArgs()))

    types = [e.type for e in result.entries]
    # All directories come before all files
    dir_indices = [i for i, t in enumerate(types) if t == "directory"]
    file_indices = [i for i, t in enumerate(types) if t == "file"]
    assert max(dir_indices) < min(file_indices)


@pytest.mark.asyncio
async def test_entries_sorted_alphabetically(ls, tmp_path):
    (tmp_path / "zebra").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "z_file.txt").write_text("z")
    (tmp_path / "a_file.txt").write_text("a")

    result = await collect_result(ls.run(LsArgs()))

    dir_names = [e.name for e in result.entries if e.type == "directory"]
    file_names = [e.name for e in result.entries if e.type == "file"]
    assert dir_names == sorted(dir_names)
    assert file_names == sorted(file_names)


@pytest.mark.asyncio
async def test_file_entries_have_size(ls, tmp_path):
    (tmp_path / "data.txt").write_text("12345")
    (tmp_path / "subdir").mkdir()

    result = await collect_result(ls.run(LsArgs()))

    for entry in result.entries:
        match entry.type:
            case "file":
                assert entry.size is not None
                assert entry.size >= 0
            case "directory":
                assert entry.size is None


@pytest.mark.asyncio
async def test_lists_subdirectory(ls, tmp_path):
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "main.py").write_text("print('hi')")
    (sub / "lib").mkdir()

    result = await collect_result(ls.run(LsArgs(path="src")))

    assert result.total_count == 2
    names = [e.name for e in result.entries]
    assert "main.py" in names
    assert "lib" in names
