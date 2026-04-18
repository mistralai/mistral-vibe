from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from vibe.core.memory.manager import MemoryManager
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.memory_read import (
    MemoryRead,
    MemoryReadArgs,
    MemoryReadConfig,
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
        "vibe.core.tools.builtins.memory_read.MemoryManager", PatchedManager
    )
    config = MemoryReadConfig()
    return MemoryRead(config=config, state=BaseToolState())


@pytest.fixture
def manager(memory_dirs):
    global_dir, project_dir = memory_dirs
    return MemoryManager(global_dir=global_dir, project_dir=project_dir)


@pytest.mark.asyncio
async def test_reads_all_memories(tool, manager):
    manager.write_memory(name="mem_a", type="user", description="A", content="Content A")
    manager.write_memory(name="mem_b", type="project", description="B", content="Content B")

    result = await collect_result(tool.run(MemoryReadArgs()))
    assert result.total_count == 2
    names = {m.name for m in result.memories}
    assert names == {"mem_a", "mem_b"}


@pytest.mark.asyncio
async def test_reads_specific_memory_by_name(tool, manager):
    manager.write_memory(
        name="specific_mem", type="feedback", description="specific", content="Found me"
    )

    result = await collect_result(tool.run(MemoryReadArgs(name="specific_mem")))
    assert result.total_count == 1
    assert result.memories[0].content == "Found me"


@pytest.mark.asyncio
async def test_returns_empty_when_no_memories(tool):
    result = await collect_result(tool.run(MemoryReadArgs()))
    assert result.total_count == 0
    assert result.memories == []


@pytest.mark.asyncio
async def test_raises_error_for_nonexistent_memory(tool):
    with pytest.raises(ToolError, match="Memory not found"):
        await collect_result(tool.run(MemoryReadArgs(name="does_not_exist")))


@pytest.mark.asyncio
async def test_reads_project_scope_only(tool, manager):
    manager.write_memory(
        name="proj_mem", type="project", description="proj", content="P", scope="project"
    )
    manager.write_memory(
        name="glob_mem", type="user", description="glob", content="G", scope="global"
    )

    result = await collect_result(tool.run(MemoryReadArgs(scope="project")))
    assert result.total_count == 1
    assert result.memories[0].name == "proj_mem"


@pytest.mark.asyncio
async def test_reads_global_scope_only(tool, manager):
    manager.write_memory(
        name="proj_mem", type="project", description="proj", content="P", scope="project"
    )
    manager.write_memory(
        name="glob_mem", type="user", description="glob", content="G", scope="global"
    )

    result = await collect_result(tool.run(MemoryReadArgs(scope="global")))
    assert result.total_count == 1
    assert result.memories[0].name == "glob_mem"
