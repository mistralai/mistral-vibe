from __future__ import annotations

from pathlib import Path

from acp import PromptRequest
from acp.schema import (
    EmbeddedResourceContentBlock,
    ResourceContentBlock,
    SessionInfoUpdate,
    TextContentBlock,
    TextResourceContents,
)
import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent as VibeAcpAgentLoop
from vibe.app_server.models import (
    PublicMessageEntry,
    ResourceContentBlock as AppServerResourceContentBlock,
)
from vibe.core.types import Role
from vibe.user_content import UserTextResource


class TestACPContent:
    @pytest.mark.asyncio
    async def test_text_content(
        self, acp_agent_loop: VibeAcpAgentLoop, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        prompt_request = PromptRequest(
            prompt=[TextContentBlock(type="text", text="Say hi")],
            session_id=session_response.session_id,
        )

        response = await acp_agent_loop.prompt(
            prompt=prompt_request.prompt, session_id=session_response.session_id
        )

        assert response.stop_reason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        assert user_message.content == "Say hi"

    @pytest.mark.asyncio
    async def test_resource_content(
        self, acp_agent_loop: VibeAcpAgentLoop, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        response = await acp_agent_loop.prompt(
            prompt=[
                TextContentBlock(type="text", text="What does this file do?"),
                EmbeddedResourceContentBlock(
                    type="resource",
                    resource=TextResourceContents(
                        uri="file:///home/my_file.py",
                        text="def hello():\n    print('Hello, world!')",
                        mime_type="text/x-python",
                    ),
                ),
            ],
            session_id=session_response.session_id,
        )

        assert response.stop_reason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        expected_content = (
            "What does this file do?"
            + "\n\npath: file:///home/my_file.py"
            + "\ncontent: def hello():\n    print('Hello, world!')"
        )
        assert user_message.content == expected_content
        session = acp_agent_loop.sessions[session_response.session_id]
        public_message = next(
            entry
            for entry in session.app_server.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "user"
        )
        resource = next(
            block
            for block in public_message.content
            if isinstance(block, AppServerResourceContentBlock)
        )
        assert isinstance(resource.resource, UserTextResource)
        assert resource.resource.text == "def hello():\n    print('Hello, world!')"

    @pytest.mark.asyncio
    async def test_resource_link_content(
        self, acp_agent_loop: VibeAcpAgentLoop, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        response = await acp_agent_loop.prompt(
            prompt=[
                TextContentBlock(type="text", text="Analyze this resource"),
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///home/document.pdf",
                    name="document.pdf",
                    title="Important Document",
                    description="A PDF document containing project specifications",
                    mime_type="application/pdf",
                    size=1024,
                ),
            ],
            session_id=session_response.session_id,
        )

        assert response.stop_reason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        expected_content = (
            "Analyze this resource"
            + "\n\nuri: file:///home/document.pdf"
            + "\nname: document.pdf"
            + "\ntitle: Important Document"
            + "\ndescription: A PDF document containing project specifications"
            + "\nmime_type: application/pdf"
            + "\nsize: 1024"
        )
        assert user_message.content == expected_content

    @pytest.mark.asyncio
    async def test_resource_link_minimal(
        self, acp_agent_loop: VibeAcpAgentLoop, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        response = await acp_agent_loop.prompt(
            prompt=[
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///home/minimal.txt",
                    name="minimal.txt",
                )
            ],
            session_id=session_response.session_id,
        )

        assert response.stop_reason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        expected_content = "uri: file:///home/minimal.txt\nname: minimal.txt"
        assert user_message.content == expected_content

    @pytest.mark.asyncio
    async def test_resource_blocks_preserve_auto_title_order(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        agent, client = acp_agent_with_session_config
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        await agent.prompt(
            session_id=session.session_id,
            prompt=[
                TextContentBlock(type="text", text="Refactor "),
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///workspace/auth.py#L2-L4",
                    name="auth.py",
                ),
                TextContentBlock(type="text", text=" please"),
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///workspace/automatic.py",
                    name="automatic.py",
                    field_meta={"automatic": True},
                ),
            ],
        )

        updates = [
            notification.update
            for notification in client._session_updates
            if isinstance(notification.update, SessionInfoUpdate)
        ]
        assert updates[-1].title == "Refactor @auth.py:2-4 please"
