"""Integration tests for the rewind feature through agent_loop.act().

These tests drive the full pipeline:
  act() → create_checkpoint() → _process_one_tool_call() →
  get_file_snapshot() → add_snapshot() → tool writes file →
  rewind_to_message() → verify file restoration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import BaseEvent, FunctionCall, Role, ToolCall


async def _act_and_collect(agent_loop, prompt: str) -> list[BaseEvent]:
    return [ev async for ev in agent_loop.act(prompt)]


def _write_file_tool_call(
    path: str, content: str, *, call_id: str = "call_1"
) -> ToolCall:
    args = json.dumps({"file_path": path, "content": content})
    return ToolCall(
        id=call_id, index=0, function=FunctionCall(name="write_file", arguments=args)
    )


def _edit_tool_call(
    file_path: str, old_string: str, new_string: str, *, call_id: str = "call_1"
) -> ToolCall:
    args = json.dumps({
        "file_path": file_path,
        "old_string": old_string,
        "new_string": new_string,
    })
    return ToolCall(
        id=call_id, index=0, function=FunctionCall(name="edit", arguments=args)
    )


def _bash_tool_call(command: str, *, call_id: str = "call_1") -> ToolCall:
    args = json.dumps({"command": command})
    return ToolCall(
        id=call_id, index=0, function=FunctionCall(name="bash", arguments=args)
    )


def _make_agent_loop(backend: FakeBackend):
    config = build_test_vibe_config(
        enabled_tools=["write_file", "edit", "bash"],
        tools={
            "write_file": {"permission": "always"},
            "edit": {"permission": "always"},
            "bash": {"permission": "always"},
        },
    )
    return build_test_agent_loop(
        config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, backend=backend
    )


@pytest.mark.asyncio
class TestRewindIntegration:
    async def test_write_file_rewind_restores_original(
        self, tmp_working_directory: Path
    ) -> None:
        """Write a file in turn 1, rewind to turn 1 → file should not exist."""
        target = tmp_working_directory / "hello.txt"

        backend = FakeBackend([
            [
                mock_llm_chunk(
                    content="Creating file.",
                    tool_calls=[_write_file_tool_call(str(target), "hello world")],
                )
            ],
            [mock_llm_chunk(content="Done.")],
        ])
        agent_loop = _make_agent_loop(backend)

        await _act_and_collect(agent_loop, "create hello.txt")
        assert target.read_text() == "hello world"

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        assert len(rewindable) == 1
        await rm.rewind_to_message(rewindable[0][0], restore_files=True)

        assert not target.exists()

    async def test_edit_rewind_restores_previous_version(
        self, tmp_working_directory: Path
    ) -> None:
        """Edit a pre-existing file with edit, rewind restores original."""
        target = tmp_working_directory / "config.yaml"
        target.write_text("key: original\n", encoding="utf-8")

        backend = FakeBackend([
            [
                mock_llm_chunk(
                    content="Updating config.",
                    tool_calls=[
                        _edit_tool_call(str(target), "key: original", "key: modified")
                    ],
                )
            ],
            [mock_llm_chunk(content="Updated.")],
        ])
        agent_loop = _make_agent_loop(backend)

        await _act_and_collect(agent_loop, "update config")
        assert target.read_text() == "key: modified\n"

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        await rm.rewind_to_message(rewindable[0][0], restore_files=True)

        assert target.read_text() == "key: original\n"

    async def test_write_then_edit_rewind_to_middle(
        self, tmp_working_directory: Path
    ) -> None:
        """Turn 1 creates a file with write_file, turn 2 patches it with
        edit. Rewind to turn 2 restores the turn 1 version.
        """
        target = tmp_working_directory / "app.py"

        backend = FakeBackend([
            # Turn 1: create the file
            [
                mock_llm_chunk(
                    content="Creating.",
                    tool_calls=[
                        _write_file_tool_call(str(target), "def main():\n    pass\n")
                    ],
                )
            ],
            [mock_llm_chunk(content="Created.")],
            # Turn 2: patch with edit
            [
                mock_llm_chunk(
                    content="Updating.",
                    tool_calls=[
                        _edit_tool_call(
                            str(target),
                            "    pass",
                            '    print("hello")',
                            call_id="call_2",
                        )
                    ],
                )
            ],
            [mock_llm_chunk(content="Updated.")],
        ])
        agent_loop = _make_agent_loop(backend)

        await _act_and_collect(agent_loop, "create app.py")
        assert "pass" in target.read_text()

        await _act_and_collect(agent_loop, "update app.py")
        assert 'print("hello")' in target.read_text()

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        assert len(rewindable) == 2

        await rm.rewind_to_message(rewindable[1][0], restore_files=True)
        assert target.read_text() == "def main():\n    pass\n"

    async def test_rewind_without_restore_keeps_files(
        self, tmp_working_directory: Path
    ) -> None:
        """Rewind with restore_files=False keeps the file as-is."""
        target = tmp_working_directory / "data.json"

        backend = FakeBackend([
            [
                mock_llm_chunk(
                    content="Writing.",
                    tool_calls=[_write_file_tool_call(str(target), '{"a": 1}')],
                )
            ],
            [mock_llm_chunk(content="Done.")],
        ])
        agent_loop = _make_agent_loop(backend)

        await _act_and_collect(agent_loop, "write data")
        assert target.read_text() == '{"a": 1}'

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        await rm.rewind_to_message(rewindable[0][0], restore_files=False)

        assert target.read_text() == '{"a": 1}'

    async def test_rewind_then_new_turn(self, tmp_working_directory: Path) -> None:
        """After rewind, a new turn creates fresh checkpoints that work correctly."""
        target = tmp_working_directory / "code.py"

        backend = FakeBackend([
            # Turn 1
            [
                mock_llm_chunk(
                    content="v1.", tool_calls=[_write_file_tool_call(str(target), "v1")]
                )
            ],
            [mock_llm_chunk(content="ok")],
            # Turn 2
            [
                mock_llm_chunk(
                    content="v2.",
                    tool_calls=[
                        _edit_tool_call(str(target), "v1", "v2", call_id="call_2")
                    ],
                )
            ],
            [mock_llm_chunk(content="ok")],
            # Turn 3 (after rewind, new turn)
            [
                mock_llm_chunk(
                    content="v2bis.",
                    tool_calls=[
                        _edit_tool_call(str(target), "v1", "v2bis", call_id="call_3")
                    ],
                )
            ],
            [mock_llm_chunk(content="ok")],
        ])
        agent_loop = _make_agent_loop(backend)

        await _act_and_collect(agent_loop, "turn1")
        await _act_and_collect(agent_loop, "turn2")
        assert target.read_text() == "v2"

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        await rm.rewind_to_message(rewindable[1][0], restore_files=True)
        assert target.read_text() == "v1"

        await _act_and_collect(agent_loop, "turn2bis")
        assert target.read_text() == "v2bis"

        rewindable = rm.get_rewindable_messages()
        await rm.rewind_to_message(rewindable[1][0], restore_files=True)
        assert target.read_text() == "v1"

    async def test_rewind_restores_file_deleted_by_bash(
        self, tmp_working_directory: Path
    ) -> None:
        """A tracked file deleted via bash in turn 2 is restored on rewind.

        Turn 1: create the file with write_file (file becomes tracked).
        Turn 2: delete it with bash (bash has no snapshot, but
                 create_checkpoint re-reads known files).
        Rewind to turn 2 → file restored to its turn-1 content.
        """
        target = tmp_working_directory / "important.txt"

        backend = FakeBackend([
            # Turn 1: create the file
            [
                mock_llm_chunk(
                    content="Creating.",
                    tool_calls=[_write_file_tool_call(str(target), "precious data")],
                )
            ],
            [mock_llm_chunk(content="Created.")],
            # Turn 2: delete it via bash
            [
                mock_llm_chunk(
                    content="Deleting.",
                    tool_calls=[_bash_tool_call(f"rm {target}", call_id="call_2")],
                )
            ],
            [mock_llm_chunk(content="Deleted.")],
        ])
        agent_loop = _make_agent_loop(backend)

        await _act_and_collect(agent_loop, "create file")
        assert target.read_text() == "precious data"

        await _act_and_collect(agent_loop, "delete file")
        assert not target.exists()

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        assert len(rewindable) == 2

        # Rewind to turn 2 → restores the file to its state before turn 2
        await rm.rewind_to_message(rewindable[1][0], restore_files=True)
        assert target.exists()
        assert target.read_text() == "precious data"

    async def test_rewind_in_place_then_reprompt_persists_to_disk(
        self, tmp_path: Path, tmp_working_directory: Path
    ) -> None:
        config = build_test_vibe_config(
            session_logging=SessionLoggingConfig(
                save_dir=str(tmp_path / "sessions"), session_prefix="test", enabled=True
            )
        )
        backend = FakeBackend([
            [mock_llm_chunk(content="response A")],
            [mock_llm_chunk(content="response A-bis")],
        ])
        agent_loop = build_test_agent_loop(
            config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, backend=backend
        )

        await _act_and_collect(agent_loop, "message A")

        rm = agent_loop.rewind_manager
        rewindable = rm.get_rewindable_messages()
        assert len(rewindable) == 1
        await rm.rewind_to_message(rewindable[0][0], restore_files=False, inplace=True)

        await _act_and_collect(agent_loop, "message A-bis")

        # Reloading from disk simulates closing and reopening the session.
        session_dir = agent_loop.session_logger.session_dir
        assert session_dir is not None
        loaded, _ = SessionLoader.load_session(session_dir)
        user_contents = [m.content for m in loaded if m.role == Role.user]
        assert user_contents == ["message A-bis"]
