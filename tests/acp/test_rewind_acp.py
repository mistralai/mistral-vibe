from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from acp.schema import TextContentBlock
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError, SessionNotFoundError
from vibe.core.rewind import RewindError
from vibe.core.types import LLMMessage, Role


async def _new_session_id(acp_agent: VibeAcpAgentLoop) -> str:
    response = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    return response.session_id


@pytest.mark.asyncio
class TestRewindAcp:
    async def test_rewind_preview_returns_restorable_paths(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        rewind_manager.index_for_message_id = lambda message_id: 3
        rewind_manager.restorable_paths_at = lambda message_index: (
            ["a.py", "b.py"] if message_index == 3 else []
        )

        result = await acp_agent.ext_method(
            "rewind/preview", {"sessionId": session_id, "messageId": "m3"}
        )

        assert result == {"paths": ["a.py", "b.py"]}

    async def test_rewind_preview_unknown_message_raises(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager

        def _raise(message_id: str) -> int:
            raise RewindError(f"No rewindable user message with id: {message_id}")

        rewind_manager.index_for_message_id = _raise

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "rewind/preview", {"sessionId": session_id, "messageId": "ghost"}
            )

    async def test_rewind_to_invokes_engine_and_returns_result(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        rewind_manager.index_for_message_id = lambda message_id: 1
        rewind_manager.restorable_paths_at = lambda message_index: ["x.py", "y.py"]
        rewind_manager.rewind_to_message = AsyncMock(
            return_value=("first", ["could not restore x"], ["y.py"])
        )

        result = await acp_agent.ext_method(
            "rewind/to",
            {"sessionId": session_id, "messageId": "m1", "restoreFiles": True},
        )

        rewind_manager.rewind_to_message.assert_awaited_once_with(
            1, restore_files=True, inplace=True
        )
        assert result == {
            "messageContent": "first",
            "restoreErrors": ["could not restore x"],
            "restoredPaths": ["y.py"],
        }

    async def test_rewind_to_defaults_restore_files_true(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        rewind_manager.index_for_message_id = lambda message_id: 1
        rewind_manager.restorable_paths_at = lambda message_index: []
        rewind_manager.rewind_to_message = AsyncMock(return_value=("first", [], []))

        await acp_agent.ext_method(
            "rewind/to", {"sessionId": session_id, "messageId": "m1"}
        )

        rewind_manager.rewind_to_message.assert_awaited_once_with(
            1, restore_files=True, inplace=True
        )

    async def test_rewind_to_without_restore_skips_paths(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        rewind_manager.index_for_message_id = lambda message_id: 1
        rewind_manager.restorable_paths_at = AsyncMock()  # must not be called
        rewind_manager.rewind_to_message = AsyncMock(return_value=("first", [], []))

        result = await acp_agent.ext_method(
            "rewind/to",
            {"sessionId": session_id, "messageId": "m1", "restoreFiles": False},
        )

        rewind_manager.rewind_to_message.assert_awaited_once_with(
            1, restore_files=False, inplace=True
        )
        rewind_manager.restorable_paths_at.assert_not_called()
        assert result["restoredPaths"] == []

    async def test_rewind_to_propagates_rewind_error(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        rewind_manager.index_for_message_id = lambda message_id: 99
        rewind_manager.restorable_paths_at = lambda message_index: []
        rewind_manager.rewind_to_message = AsyncMock(
            side_effect=RewindError("bad index")
        )

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method(
                "rewind/to", {"sessionId": session_id, "messageId": "m99"}
            )

    async def test_rewind_to_rejects_active_prompt(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        session = acp_agent.sessions[session_id]

        async def pending_prompt() -> None:
            await asyncio.Event().wait()

        session.set_prompt_task(pending_prompt())

        try:
            with pytest.raises(InvalidRequestError, match="agent loop is running"):
                await acp_agent.ext_method(
                    "rewind/to", {"sessionId": session_id, "messageId": "m1"}
                )
        finally:
            await session.cancel_prompt()

    async def test_concurrent_rewind_to_calls_are_serialized(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        calls: list[str] = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        def index_for_message_id(message_id: str) -> int:
            calls.append(f"index:{message_id}")
            return 1

        async def rewind_to_message(
            message_index: int, *, restore_files: bool, inplace: bool = False
        ) -> tuple[str, list[str], list[str]]:
            calls.append(f"start:{restore_files}")
            if restore_files:
                first_entered.set()
                await release_first.wait()
            calls.append(f"end:{restore_files}")
            return ("first", [], [])

        rewind_manager.index_for_message_id = index_for_message_id
        rewind_manager.rewind_to_message = rewind_to_message

        first = asyncio.create_task(
            acp_agent.ext_method(
                "rewind/to",
                {"sessionId": session_id, "messageId": "m1", "restoreFiles": True},
            )
        )
        await first_entered.wait()
        second = asyncio.create_task(
            acp_agent.ext_method(
                "rewind/to",
                {"sessionId": session_id, "messageId": "m2", "restoreFiles": False},
            )
        )
        await asyncio.sleep(0)

        assert calls == ["index:m1", "start:True"]

        release_first.set()
        await asyncio.gather(first, second)

        assert calls == [
            "index:m1",
            "start:True",
            "end:True",
            "index:m2",
            "start:False",
            "end:False",
        ]

    async def test_prompt_does_not_start_agent_loop_during_rewind(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        session = acp_agent.sessions[session_id]

        async with session.mutating("rewind a session"):
            prompt_task = asyncio.create_task(
                acp_agent.prompt(
                    session_id=session_id,
                    prompt=[TextContentBlock(type="text", text="Hello")],
                )
            )
            for _ in range(5):
                await asyncio.sleep(0)
            assert session.prompt_task is None
            assert not prompt_task.done()

        await prompt_task
        assert session.prompt_task is None

    async def test_rewind_preview_waits_for_in_flight_rewind(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        rewind_manager = acp_agent.sessions[session_id].agent_loop.rewind_manager
        calls: list[str] = []
        rewind_entered = asyncio.Event()
        release_rewind = asyncio.Event()

        def index_for_message_id(message_id: str) -> int:
            calls.append(f"index:{message_id}")
            return 1

        async def rewind_to_message(
            message_index: int, *, restore_files: bool, inplace: bool = False
        ) -> tuple[str, list[str], list[str]]:
            calls.append("rewind:start")
            rewind_entered.set()
            await release_rewind.wait()
            calls.append("rewind:end")
            return ("first", [], [])

        def restorable_paths_at(message_index: int) -> list[str]:
            calls.append("preview:paths")
            return ["a.py"]

        rewind_manager.index_for_message_id = index_for_message_id
        rewind_manager.rewind_to_message = rewind_to_message
        rewind_manager.restorable_paths_at = restorable_paths_at

        rewind = asyncio.create_task(
            acp_agent.ext_method(
                "rewind/to",
                {"sessionId": session_id, "messageId": "m1", "restoreFiles": True},
            )
        )
        await rewind_entered.wait()
        preview = asyncio.create_task(
            acp_agent.ext_method(
                "rewind/preview", {"sessionId": session_id, "messageId": "m2"}
            )
        )
        await asyncio.sleep(0)

        assert calls == ["index:m1", "rewind:start"]

        release_rewind.set()
        _, preview_result = await asyncio.gather(rewind, preview)

        assert calls == [
            "index:m1",
            "rewind:start",
            "rewind:end",
            "index:m2",
            "preview:paths",
        ]
        assert preview_result == {"paths": ["a.py"]}

    async def test_fork_waits_for_in_flight_rewind(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        session = acp_agent.sessions[session_id]
        rewind_manager = session.agent_loop.rewind_manager
        order: list[str] = []
        rewind_entered = asyncio.Event()
        release_rewind = asyncio.Event()

        rewind_manager.index_for_message_id = lambda message_id: 1

        async def rewind_to_message(
            message_index: int, *, restore_files: bool, inplace: bool = False
        ) -> tuple[str, list[str], list[str]]:
            order.append("rewind:start")
            rewind_entered.set()
            await release_rewind.wait()
            order.append("rewind:end")
            return ("first", [], [])

        rewind_manager.rewind_to_message = rewind_to_message

        # Record entry then abort; we only assert the fork body runs after rewind.
        async def fork(message_id: str | None) -> object:
            order.append("fork")
            raise ValueError("stop")

        monkeypatch.setattr(session.agent_loop, "fork", fork)

        rewind = asyncio.create_task(
            acp_agent.ext_method(
                "rewind/to",
                {"sessionId": session_id, "messageId": "m1", "restoreFiles": True},
            )
        )
        await rewind_entered.wait()
        fork_task = asyncio.create_task(
            acp_agent.fork_session(
                cwd=str(Path.cwd()), session_id=session_id, mcp_servers=[]
            )
        )
        await asyncio.sleep(0)

        assert order == ["rewind:start"]

        release_rewind.set()
        await rewind
        with pytest.raises(InvalidRequestError):
            await fork_task

        assert order == ["rewind:start", "rewind:end", "fork"]

    async def test_compact_waits_for_in_flight_rewind(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]
        session_id = await _new_session_id(acp_agent)
        session = acp_agent.sessions[session_id]
        await session.agent_loop.wait_until_ready()
        session.agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        rewind_manager = session.agent_loop.rewind_manager
        order: list[str] = []
        rewind_entered = asyncio.Event()
        release_rewind = asyncio.Event()

        rewind_manager.index_for_message_id = lambda message_id: 1

        async def rewind_to_message(
            message_index: int, *, restore_files: bool, inplace: bool = False
        ) -> tuple[str, list[str], list[str]]:
            order.append("rewind:start")
            rewind_entered.set()
            await release_rewind.wait()
            order.append("rewind:end")
            return ("first", [], [])

        rewind_manager.rewind_to_message = rewind_to_message

        async def compact(*, extra_instructions: str = "") -> None:
            order.append("compact")

        session.agent_loop.compact = compact

        rewind = asyncio.create_task(
            acp_agent.ext_method(
                "rewind/to",
                {"sessionId": session_id, "messageId": "m1", "restoreFiles": True},
            )
        )
        await rewind_entered.wait()
        compact_task = asyncio.create_task(
            acp_agent.prompt(
                session_id=session_id,
                prompt=[TextContentBlock(type="text", text="/compact")],
            )
        )
        await asyncio.sleep(0)

        assert order == ["rewind:start"]

        release_rewind.set()
        await asyncio.gather(rewind, compact_task)

        assert order == ["rewind:start", "rewind:end", "compact"]

    async def test_unknown_session_raises(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]

        with pytest.raises(SessionNotFoundError):
            await acp_agent.ext_method(
                "rewind/to", {"sessionId": "missing", "messageId": "m1"}
            )

    async def test_invalid_params_raise(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent = acp_agent_with_session_config[0]

        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method("rewind/preview", {"sessionId": "x"})
        with pytest.raises(InvalidRequestError):
            await acp_agent.ext_method("rewind/to", {"sessionId": "x"})
