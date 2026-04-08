from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from vibe.core.memory.manager import MemoryManager
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.memory_write import (
    MemoryWrite,
    MemoryWriteArgs,
    MemoryWriteConfig,
)


@pytest.fixture
def memory_dirs(tmp_path):
    global_dir = tmp_path / "global_memory"
    project_dir = tmp_path / "project_memory"
    return global_dir, project_dir


@pytest.fixture
def tool(memory_dirs, monkeypatch):
    global_dir, project_dir = memory_dirs

    class PatchedManager(MemoryManager):
        def __init__(self, **_kwargs):
            super().__init__(global_dir=global_dir, project_dir=project_dir)

    monkeypatch.setattr(
        "vibe.core.tools.builtins.memory_write.MemoryManager", PatchedManager
    )
    config = MemoryWriteConfig()
    return MemoryWrite(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_writes_memory_to_project_dir(tool, memory_dirs):
    _, project_dir = memory_dirs
    result = await collect_result(
        tool.run(
            MemoryWriteArgs(
                name="test_mem",
                type="feedback",
                description="A test",
                content="Test content",
                scope="project",
            )
        )
    )
    assert result.name == "test_mem"
    assert project_dir.is_dir()
    assert (project_dir / "test_mem.md").is_file()


@pytest.mark.asyncio
async def test_writes_memory_to_global_dir(tool, memory_dirs):
    global_dir, _ = memory_dirs
    result = await collect_result(
        tool.run(
            MemoryWriteArgs(
                name="global_mem",
                type="user",
                description="Global pref",
                content="Global content",
                scope="global",
            )
        )
    )
    assert result.name == "global_mem"
    assert global_dir.is_dir()
    assert (global_dir / "global_mem.md").is_file()


@pytest.mark.asyncio
async def test_raises_error_for_empty_name(tool):
    with pytest.raises(ToolError, match="name cannot be empty"):
        await collect_result(
            tool.run(
                MemoryWriteArgs(
                    name="  ",
                    type="feedback",
                    description="desc",
                    content="content",
                )
            )
        )


@pytest.mark.asyncio
async def test_raises_error_for_empty_content(tool):
    with pytest.raises(ToolError, match="content cannot be empty"):
        await collect_result(
            tool.run(
                MemoryWriteArgs(
                    name="test",
                    type="feedback",
                    description="desc",
                    content="  ",
                )
            )
        )


@pytest.mark.asyncio
async def test_raises_error_for_invalid_type(tool):
    with pytest.raises(ToolError, match="Invalid memory type"):
        await collect_result(
            tool.run(
                MemoryWriteArgs(
                    name="test",
                    type="bogus",
                    description="desc",
                    content="content",
                )
            )
        )


@pytest.mark.asyncio
async def test_raises_error_for_invalid_scope(tool):
    with pytest.raises(ToolError, match="Scope must be"):
        await collect_result(
            tool.run(
                MemoryWriteArgs(
                    name="test",
                    type="feedback",
                    description="desc",
                    content="content",
                    scope="invalid",
                )
            )
        )


@pytest.mark.asyncio
async def test_overwrites_existing_memory(tool, memory_dirs):
    _, project_dir = memory_dirs
    await collect_result(
        tool.run(
            MemoryWriteArgs(
                name="overwrite_me",
                type="feedback",
                description="v1",
                content="Original content",
            )
        )
    )
    await collect_result(
        tool.run(
            MemoryWriteArgs(
                name="overwrite_me",
                type="feedback",
                description="v2",
                content="Updated content",
            )
        )
    )

    file_text = (project_dir / "overwrite_me.md").read_text()
    assert "Updated content" in file_text
    assert "Original content" not in file_text
