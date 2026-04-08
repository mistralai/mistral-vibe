from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.mock.utils import collect_result
from vibe.core.memory.manager import MemoryManager
from vibe.core.session.session_loader import SessionInfo
from vibe.core.tools.base import BaseToolState
from vibe.core.tools.builtins.learn import Learn, LearnArgs, LearnConfig
from vibe.core.types import LLMMessage, Role


def _make_session_info(session_id: str) -> SessionInfo:
    return {
        "session_id": session_id,
        "cwd": "/tmp/project",
        "title": f"Session {session_id}",
        "end_time": "2026-04-01T12:00:00+00:00",
    }


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

    monkeypatch.setattr("vibe.core.tools.builtins.learn.MemoryManager", PatchedManager)
    config = LearnConfig()
    return Learn(config=config, state=BaseToolState())


@pytest.fixture
def session_dir(tmp_path):
    """Create a fake session directory."""
    d = tmp_path / "session_abc12345"
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_analyzes_correction_pattern(tool, session_dir):
    mock_messages = [
        LLMMessage(role=Role.assistant, content="I'll use spaces for indentation."),
        LLMMessage(role=Role.user, content="No, always use tabs instead of spaces."),
        LLMMessage(role=Role.assistant, content="Got it, I'll use tabs."),
    ]

    with (
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.list_sessions",
            return_value=[_make_session_info("abc12345")],
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.load_session",
            return_value=(mock_messages, {}),
        ),
    ):
        result = await collect_result(tool.run(LearnArgs(max_sessions=5)))

    assert result.sessions_analyzed == 1
    assert result.memories_created >= 1

    names = [m.name for m in result.memories]
    has_correction = any("correction_" in n for n in names)
    assert has_correction

    correction_mem = next(m for m in result.memories if "correction_" in m.name)
    assert correction_mem.type == "feedback"
    assert (
        "spaces" in correction_mem.content.lower()
        or "tabs" in correction_mem.content.lower()
    )


@pytest.mark.asyncio
async def test_analyzes_preference_pattern(tool, session_dir):
    mock_messages = [
        LLMMessage(role=Role.user, content="Always use pytest for testing."),
        LLMMessage(role=Role.assistant, content="Understood, I'll use pytest."),
    ]

    with (
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.list_sessions",
            return_value=[_make_session_info("def12345")],
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.load_session",
            return_value=(mock_messages, {}),
        ),
    ):
        result = await collect_result(tool.run(LearnArgs(max_sessions=5)))

    assert result.sessions_analyzed == 1
    assert result.memories_created >= 1

    has_preference = any("preference_" in m.name for m in result.memories)
    assert has_preference


@pytest.mark.asyncio
async def test_returns_empty_when_no_patterns(tool, session_dir):
    mock_messages = [
        LLMMessage(role=Role.user, content="What is the weather like?"),
        LLMMessage(role=Role.assistant, content="I don't have access to weather data."),
        LLMMessage(role=Role.user, content="Ok thanks."),
    ]

    with (
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.list_sessions",
            return_value=[_make_session_info("ghi12345")],
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.load_session",
            return_value=(mock_messages, {}),
        ),
    ):
        result = await collect_result(tool.run(LearnArgs(max_sessions=5)))

    assert result.sessions_analyzed == 1
    assert result.memories_created == 0
    assert result.memories == []


@pytest.mark.asyncio
async def test_skips_duplicate_memories(tool, session_dir, memory_dirs):
    """If a memory with the same name already exists, don't create it again."""
    _, project_dir = memory_dirs

    mock_messages = [
        LLMMessage(role=Role.user, content="Always use black for formatting."),
        LLMMessage(role=Role.assistant, content="Ok."),
    ]

    with (
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.list_sessions",
            return_value=[_make_session_info("dup12345")],
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.load_session",
            return_value=(mock_messages, {}),
        ),
    ):
        result1 = await collect_result(tool.run(LearnArgs(max_sessions=5)))

    assert result1.memories_created >= 1

    # Run again - should not create duplicates
    with (
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.list_sessions",
            return_value=[_make_session_info("dup12345")],
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.load_session",
            return_value=(mock_messages, {}),
        ),
    ):
        result2 = await collect_result(tool.run(LearnArgs(max_sessions=5)))

    assert result2.memories_created == 0


@pytest.mark.asyncio
async def test_limits_sessions_analyzed(tool, session_dir):
    sessions = [_make_session_info(f"sess{i:05d}") for i in range(20)]
    mock_messages = [
        LLMMessage(role=Role.user, content="Hello"),
        LLMMessage(role=Role.assistant, content="Hi"),
    ]

    with (
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.list_sessions",
            return_value=sessions,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.core.tools.builtins.learn.SessionLoader.load_session",
            return_value=(mock_messages, {}),
        ),
    ):
        result = await collect_result(tool.run(LearnArgs(max_sessions=3)))

    assert result.sessions_analyzed == 3


@pytest.mark.asyncio
async def test_handles_empty_sessions(tool):
    with patch(
        "vibe.core.tools.builtins.learn.SessionLoader.list_sessions", return_value=[]
    ):
        result = await collect_result(tool.run(LearnArgs(max_sessions=5)))

    assert result.sessions_analyzed == 0
    assert result.memories_created == 0
    assert result.memories == []
