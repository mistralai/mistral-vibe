from __future__ import annotations

from pathlib import Path

from acp.schema import ResourceContentBlock, SessionInfoUpdate, TextContentBlock
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop


def _info_updates(client: FakeClient) -> list:
    return [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, SessionInfoUpdate)
    ]


class TestAcpAutoTitleOnPrompt:
    @pytest.mark.asyncio
    async def test_emits_session_info_update_on_first_prompt(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        new_session = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        assert new_session is not None

        await acp_agent.prompt(
            session_id=new_session.session_id,
            prompt=[
                TextContentBlock(type="text", text="Refactor "),
                ResourceContentBlock(
                    type="resource_link", uri="file:///abs/auth.py", name="auth.py"
                ),
                TextContentBlock(type="text", text=" please"),
            ],
        )

        updates = _info_updates(client)
        assert len(updates) == 1
        assert updates[0].title == "Refactor @auth.py please"
        assert updates[0].updated_at is None

    @pytest.mark.asyncio
    async def test_no_event_on_second_prompt(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        new_session = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        assert new_session is not None

        await acp_agent.prompt(
            session_id=new_session.session_id,
            prompt=[TextContentBlock(type="text", text="first")],
        )
        assert len(_info_updates(client)) == 1

        await acp_agent.prompt(
            session_id=new_session.session_id,
            prompt=[TextContentBlock(type="text", text="second")],
        )

        assert len(_info_updates(client)) == 1

    @pytest.mark.asyncio
    async def test_skips_automatic_resource_in_title(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        new_session = await acp_agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        assert new_session is not None

        await acp_agent.prompt(
            session_id=new_session.session_id,
            prompt=[
                TextContentBlock(type="text", text="What does this do?"),
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///abs/open_in_editor.py",
                    name="open_in_editor.py",
                    field_meta={"automatic": True},
                ),
            ],
        )

        updates = _info_updates(client)
        assert len(updates) == 1
        assert updates[0].title == "What does this do?"
