from __future__ import annotations

import asyncio
import pytest
from acp.schema import CloseSessionResponse

from tests.acp.conftest import _create_acp_agent
from vibe.acp.exceptions import SessionNotFoundError


def _new_session(acp_agent, backend=None):
    return asyncio.get_event_loop().run_until_complete(
        acp_agent.new_session(cwd=str(__import__("pathlib").Path.cwd()))
    )


class TestACPCloseSession:
    @pytest.mark.asyncio
    async def test_close_session_removes_session(self, backend) -> None:
        """Basic: closing a session removes it from the session map."""
        acp_agent = _create_acp_agent()
        session_response = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        session_id = session_response.session_id
        assert session_id in acp_agent.sessions

        response = await acp_agent.close_session(session_id=session_id)
        assert isinstance(response, CloseSessionResponse)
        assert session_id not in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_close_session_cancels_active_task(self, backend) -> None:
        """Closing a session with a running task cancels that task."""
        acp_agent = _create_acp_agent()
        session_response = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        session_id = session_response.session_id
        session = acp_agent.sessions[session_id]

        async def dummy_task() -> None:
            await asyncio.sleep(10)

        session.task = asyncio.create_task(dummy_task())
        assert not session.task.done()

        response = await acp_agent.close_session(session_id=session_id)
        assert isinstance(response, CloseSessionResponse)

        try:
            await asyncio.wait_for(session.task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert session.task.cancelled()
        assert session_id not in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_close_session_with_no_task(self, backend) -> None:
        """Closing a session where task is None should not error."""
        acp_agent = _create_acp_agent()
        session_response = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        session_id = session_response.session_id
        session = acp_agent.sessions[session_id]
        assert session.task is None

        response = await acp_agent.close_session(session_id=session_id)
        assert isinstance(response, CloseSessionResponse)
        assert session_id not in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_close_session_with_completed_task(self, backend) -> None:
        """Closing a session where the task has already completed."""
        acp_agent = _create_acp_agent()
        session_response = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        session_id = session_response.session_id
        session = acp_agent.sessions[session_id]

        async def quick_task() -> str:
            await asyncio.sleep(0.01)
            return "done"

        task = asyncio.create_task(quick_task())
        await task
        session.task = task
        assert task.done()

        response = await acp_agent.close_session(session_id=session_id)
        assert isinstance(response, CloseSessionResponse)
        assert session_id not in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_close_nonexistent_session_raises(self, backend) -> None:
        """Closing a session that doesn't exist raises SessionNotFoundError."""
        acp_agent = _create_acp_agent()
        fake_id = "nonexistent-session-id"
        with pytest.raises(SessionNotFoundError):
            await acp_agent.close_session(session_id=fake_id)

    @pytest.mark.asyncio
    async def test_close_already_closed_session_raises(self, backend) -> None:
        """Closing the same session twice should raise on the second call."""
        acp_agent = _create_acp_agent()
        session_response = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        session_id = session_response.session_id

        await acp_agent.close_session(session_id=session_id)
        assert session_id not in acp_agent.sessions

        with pytest.raises(SessionNotFoundError):
            await acp_agent.close_session(session_id=session_id)

    @pytest.mark.asyncio
    async def test_close_one_session_leaves_others(self, backend) -> None:
        """Closing one session should not affect other open sessions."""
        acp_agent = _create_acp_agent()
        r1 = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        r2 = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        r3 = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )

        assert len(acp_agent.sessions) == 3
        assert r1.session_id in acp_agent.sessions
        assert r2.session_id in acp_agent.sessions
        assert r3.session_id in acp_agent.sessions

        # Close the middle session
        await acp_agent.close_session(session_id=r2.session_id)

        assert len(acp_agent.sessions) == 2
        assert r1.session_id in acp_agent.sessions
        assert r2.session_id not in acp_agent.sessions
        assert r3.session_id in acp_agent.sessions

        # Verify remaining sessions still work
        await acp_agent.close_session(session_id=r1.session_id)
        assert r1.session_id not in acp_agent.sessions
        assert r3.session_id in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_close_session_allows_new_session(self, backend) -> None:
        """After closing a session, a new session can be created."""
        acp_agent = _create_acp_agent()
        r1 = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        old_id = r1.session_id

        await acp_agent.close_session(session_id=old_id)
        assert old_id not in acp_agent.sessions

        r2 = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        assert r2.session_id not in [old_id]
        assert r2.session_id in acp_agent.sessions

    @pytest.mark.asyncio
    async def test_close_session_returns_valid_response(self, backend) -> None:
        """close_session returns a proper CloseSessionResponse with no extra fields."""
        acp_agent = _create_acp_agent()
        session_response = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        session_id = session_response.session_id

        response = await acp_agent.close_session(session_id=session_id)
        assert isinstance(response, CloseSessionResponse)
        # CloseSessionResponse should be a valid response object
        assert response is not None

    @pytest.mark.asyncio
    async def test_close_session_multiple_times_interleaved(self, backend) -> None:
        """Open and close multiple sessions in interleaved order."""
        acp_agent = _create_acp_agent()

        # Create 3 sessions
        ids = []
        for _ in range(3):
            r = await acp_agent.new_session(
                cwd=str(__import__("pathlib").Path.cwd())
            )
            ids.append(r.session_id)

        assert len(acp_agent.sessions) == 3

        # Close in reverse order
        for sid in reversed(ids):
            assert sid in acp_agent.sessions
            await acp_agent.close_session(session_id=sid)
            assert sid not in acp_agent.sessions

        assert len(acp_agent.sessions) == 0

    @pytest.mark.asyncio
    async def test_close_session_does_not_affect_agent_state(self, backend) -> None:
        """Closing a session should not break the agent itself."""
        acp_agent = _create_acp_agent()

        # Create and close a session
        r = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        await acp_agent.close_session(session_id=r.session_id)

        # Agent should still be able to accept new sessions
        r2 = await acp_agent.new_session(
            cwd=str(__import__("pathlib").Path.cwd())
        )
        assert r2.session_id is not None
        assert r2.session_id in acp_agent.sessions

        # And close it too
        await acp_agent.close_session(session_id=r2.session_id)
        assert r2.session_id not in acp_agent.sessions
