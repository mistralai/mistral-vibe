from __future__ import annotations

import pytest

from vibe.core.memory.manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    global_dir = tmp_path / "global_memory"
    project_dir = tmp_path / "project_memory"
    return MemoryManager(global_dir=global_dir, project_dir=project_dir)


def test_write_and_read_memory(manager: MemoryManager):
    manager.write_memory(
        name="test_pref",
        type="feedback",
        description="A test preference",
        content="User prefers dark mode.",
        scope="project",
    )

    mem = manager.read_memory("test_pref")
    assert mem is not None
    assert mem.name == "test_pref"
    assert mem.type == "feedback"
    assert mem.description == "A test preference"
    assert mem.content == "User prefers dark mode."


def test_list_memories_empty(manager: MemoryManager):
    assert manager.list_memories() == []


def test_list_memories_multiple(manager: MemoryManager):
    manager.write_memory(
        name="mem_a", type="user", description="A", content="Content A"
    )
    manager.write_memory(
        name="mem_b", type="project", description="B", content="Content B"
    )

    memories = manager.list_memories(scope="project")
    assert len(memories) == 2
    names = {m.name for m in memories}
    assert names == {"mem_a", "mem_b"}


def test_delete_memory(manager: MemoryManager):
    manager.write_memory(
        name="to_delete", type="feedback", description="temp", content="bye"
    )
    assert manager.read_memory("to_delete") is not None

    deleted = manager.delete_memory("to_delete")
    assert deleted is True
    assert manager.read_memory("to_delete") is None

    # Deleting non-existent returns False
    assert manager.delete_memory("to_delete") is False


def test_slugify(manager: MemoryManager):
    assert manager._slugify("Hello World") == "hello_world"
    assert manager._slugify("user prefers tabs") == "user_prefers_tabs"
    assert manager._slugify("Special!@#Chars") == "specialchars"
    assert manager._slugify("") == "memory"


def test_get_context_string(manager: MemoryManager):
    # Empty case
    assert manager.get_context_string() == ""

    manager.write_memory(
        name="ctx_test",
        type="user",
        description="test desc",
        content="Some context here.",
    )

    ctx = manager.get_context_string()
    assert "# Memories" in ctx
    assert "[user] ctx_test" in ctx
    assert "Some context here." in ctx


def test_invalid_memory_type(manager: MemoryManager):
    with pytest.raises(ValueError, match="Invalid memory type"):
        manager.write_memory(
            name="bad", type="invalid_type", description="nope", content="nope"
        )
