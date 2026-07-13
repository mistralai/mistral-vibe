"""Tests for ACP slash command handlers on VibeAcpAgentLoop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    DeniedOutcome,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.skills.conftest import create_skill
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.teleport import TELEPORT_PUSH_OPTION_ID
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig
from vibe.core.config.orchestrator_legacy import LegacyConfigOrchestrator
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.teleport import TeleportService
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
)
from vibe.core.types import LLMMessage, Role
from vibe.core.vibe_code_project import VibeCodeProjectResolverError


def _get_client(agent: VibeAcpAgentLoop) -> FakeClient:
    assert isinstance(agent.client, FakeClient)
    return agent.client


def _get_message_texts(agent: VibeAcpAgentLoop) -> list[str]:
    """Extract text content from all AgentMessageChunk session updates."""
    return [
        u.update.content.text
        for u in _get_client(agent)._session_updates
        if isinstance(u.update, AgentMessageChunk)
    ]


def _get_tool_updates(
    agent: VibeAcpAgentLoop,
) -> list[ToolCallStart | ToolCallProgress]:
    return [
        u.update
        for u in _get_client(agent)._session_updates
        if isinstance(u.update, (ToolCallStart, ToolCallProgress))
    ]


def _set_teleport_service(agent_loop: AgentLoop, service: object) -> None:
    agent_loop._teleport_service = cast(TeleportService, service)


async def _new_session_and_clear(agent: VibeAcpAgentLoop) -> str:
    """Create a new session, drain the startup updates, return session_id."""
    resp = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    await _wait_for_available_commands(agent)
    _get_client(agent)._session_updates.clear()
    return resp.session_id


async def _wait_for_available_commands(agent: VibeAcpAgentLoop) -> None:
    for _ in range(50):
        updates = _get_client(agent)._session_updates
        if any(isinstance(u.update, AvailableCommandsUpdate) for u in updates):
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("available commands update was not sent")


async def _prompt(agent: VibeAcpAgentLoop, session_id: str, text: str):
    return await agent.prompt(
        prompt=[TextContentBlock(type="text", text=text)], session_id=session_id
    )


def _make_patched_agent_loop(
    backend: FakeBackend,
    *,
    skill_paths: list[Path] | None = None,
    session_logging: SessionLoggingConfig | None = None,
    vibe_code_enabled: bool | None = None,
) -> type[AgentLoop]:
    """Create a PatchedAgentLoop class that injects config overrides."""
    config_updates: dict = {}
    if skill_paths is not None:
        config_updates["skill_paths"] = skill_paths
    if session_logging is not None:
        config_updates["session_logging"] = session_logging
    if vibe_code_enabled is not None:
        config_updates["vibe_code_enabled"] = vibe_code_enabled

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            orchestrator = kwargs.get("config_orchestrator")
            if config_updates and orchestrator is not None:
                kwargs["config_orchestrator"] = LegacyConfigOrchestrator(
                    orchestrator.config.model_copy(update=config_updates)
                )
            super().__init__(*args, **{**kwargs, "backend": backend})

    return PatchedAgentLoop


@pytest.fixture
def acp_agent_loop(backend: FakeBackend) -> VibeAcpAgentLoop:
    patched = _make_patched_agent_loop(backend)
    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched).start()
    return _create_acp_agent()


@pytest.fixture
def acp_agent_loop_vibe_code_disabled(backend: FakeBackend) -> VibeAcpAgentLoop:
    patched = _make_patched_agent_loop(backend, vibe_code_enabled=False)
    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched).start()
    return _create_acp_agent()


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    return d


@pytest.fixture
def acp_agent_loop_with_skills(
    backend: FakeBackend, skills_dir: Path
) -> VibeAcpAgentLoop:
    # Skills must exist in skills_dir BEFORE new_session() is called.
    patched = _make_patched_agent_loop(backend, skill_paths=[skills_dir])
    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched).start()
    return _create_acp_agent()


class TestHandleHelp:
    @pytest.mark.asyncio
    async def test_lists_all_registered_commands(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/help")

        assert response.stop_reason == "end_turn"
        texts = _get_message_texts(acp_agent_loop)
        assert len(texts) == 1
        content = texts[0]

        main_commands = ["help", "compact", "reload", "proxy-setup"]
        for cmd in main_commands:
            assert f"/{cmd}" in content

    @pytest.mark.asyncio
    async def test_lists_registered_commands_alphabetically(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        await _prompt(acp_agent_loop, session_id, "/help")

        content = _get_message_texts(acp_agent_loop)[0]
        commands_section = content.split("### Available Commands\n\n", maxsplit=1)[
            1
        ].split("\n\n", maxsplit=1)[0]
        command_names = [
            line.split("`", maxsplit=2)[1].removeprefix("/")
            for line in commands_section.splitlines()
            if line.startswith("- ")
        ]

        assert command_names == sorted(command_names)

    @pytest.mark.asyncio
    async def test_includes_user_invocable_skills(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        # Create skills before new_session so SkillManager discovers them
        create_skill(skills_dir, "my-skill", "Does something useful")
        create_skill(skills_dir, "hidden-skill", "Secret", user_invocable=False)

        session_id = await _new_session_and_clear(acp_agent_loop_with_skills)
        await _prompt(acp_agent_loop_with_skills, session_id, "/help")

        content = _get_message_texts(acp_agent_loop_with_skills)[0]
        assert "/my-skill" in content
        assert "Does something useful" in content
        assert "hidden-skill" not in content


class TestHandleCompact:
    @pytest.mark.asyncio
    async def test_empty_history_does_not_compact(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]

        with patch.object(
            session.agent_loop, "compact", new_callable=AsyncMock
        ) as mock_compact:
            await _prompt(acp_agent_loop, session_id, "/compact")
            mock_compact.assert_not_called()

    @pytest.mark.asyncio
    async def test_compact_calls_agent_loop_compact(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)

        # Have a conversation first to create history
        await _prompt(acp_agent_loop, session_id, "Hello, tell me something")
        _get_client(acp_agent_loop)._session_updates.clear()

        session = acp_agent_loop.sessions[session_id]
        with patch.object(
            session.agent_loop, "compact", new_callable=AsyncMock
        ) as mock_compact:
            response = await _prompt(acp_agent_loop, session_id, "/compact")
            assert response.stop_reason == "end_turn"
            mock_compact.assert_called_once()


class TestHandleTeleport:
    @pytest.mark.asyncio
    async def test_available_commands_includes_teleport(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        await _wait_for_available_commands(acp_agent_loop)

        available = [
            u
            for u in _get_client(acp_agent_loop)._session_updates
            if isinstance(u.update, AvailableCommandsUpdate)
        ]
        cmd_names = [c.name for c in available[0].update.available_commands]

        assert "teleport" in cmd_names

    @pytest.mark.asyncio
    async def test_teleport_hidden_when_vibe_code_disabled(
        self, acp_agent_loop_vibe_code_disabled: VibeAcpAgentLoop
    ) -> None:
        agent = acp_agent_loop_vibe_code_disabled
        await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        await _wait_for_available_commands(agent)

        available = [
            u
            for u in _get_client(agent)._session_updates
            if isinstance(u.update, AvailableCommandsUpdate)
        ]
        cmd_names = [c.name for c in available[0].update.available_commands]

        assert "teleport" not in cmd_names

    @pytest.mark.asyncio
    async def test_teleport_without_history_replies_with_no_history(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)

        response = await _prompt(acp_agent_loop, session_id, "/teleport")

        assert response.stop_reason == "end_turn"
        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {"status": "no_history"},
        }
        assert _get_message_texts(acp_agent_loop) == [
            "No conversation history to teleport."
        ]
        assert _get_tool_updates(acp_agent_loop) == []

    @pytest.mark.asyncio
    async def test_teleport_replies_with_error_when_model_not_mistral(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)

        with patch(
            "vibe.core.config._settings.VibeConfig.is_active_model_mistral",
            return_value=False,
        ):
            response = await _prompt(acp_agent_loop, session_id, "/teleport")

        assert response.stop_reason == "end_turn"
        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {"status": "unavailable"},
        }
        texts = _get_message_texts(acp_agent_loop)
        assert len(texts) == 1
        assert "active Mistral model" in texts[0]
        assert _get_tool_updates(acp_agent_loop) == []

    @pytest.mark.asyncio
    async def test_teleport_sends_tool_updates_and_structured_url(
        self, acp_agent_loop: VibeAcpAgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeTeleportService:
            prompts: list[str]
            project_ids: list[str | None]

            def __init__(self) -> None:
                self.prompts = []
                self.project_ids = []

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, prompt: str, *, project_id: str | None = None, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                self.prompts.append(prompt)
                self.project_ids.append(project_id)
                yield TeleportCheckingGitEvent()
                yield TeleportStartingWorkflowEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/code/1/2")

        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]
        session.agent_loop.messages.append(
            LLMMessage(role=Role.user, content="continue this task")
        )
        monkeypatch.setattr(
            "vibe.acp.teleport._resolve_acp_teleport_project",
            AsyncMock(return_value=(None, None)),
        )
        service = FakeTeleportService()
        _set_teleport_service(session.agent_loop, service)

        response = await _prompt(acp_agent_loop, session_id, "/teleport ignored")

        assert response.stop_reason == "end_turn"
        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {
                "status": "completed",
                "url": "https://chat.example.com/code/1/2",
            },
        }
        assert service.prompts == ["continue this task"]
        assert service.project_ids == [None]
        assert _get_message_texts(acp_agent_loop) == []

        tool_updates = _get_tool_updates(acp_agent_loop)
        assert isinstance(tool_updates[0], ToolCallStart)
        assert tool_updates[0].title == "Teleporting session to Vibe Code Web..."
        assert tool_updates[-1].status == "completed"
        assert tool_updates[-1].title == "Teleported to Vibe Code Web"
        assert [update.field_meta for update in tool_updates] == [
            {"tool_name": "teleport", "teleport": {"status": "starting"}},
            {"tool_name": "teleport", "teleport": {"status": "preparing_workspace"}},
            {"tool_name": "teleport", "teleport": {"status": "starting_workflow"}},
            {
                "tool_name": "teleport",
                "teleport": {
                    "status": "completed",
                    "url": "https://chat.example.com/code/1/2",
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_teleport_uses_headless_resolved_project_id(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        monkeypatch: pytest.MonkeyPatch,
        telemetry_events: list[dict],
    ) -> None:
        class FakeTeleportService:
            def __init__(self) -> None:
                self.project_ids: list[str | None] = []

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, prompt: str, *, project_id: str | None = None, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                self.project_ids.append(project_id)
                yield TeleportCompleteEvent(url="https://chat.example.com/code/1/2")

        async def resolve_project(_session: object) -> tuple[str, dict[str, object]]:
            return "project-id", {
                "project_picker_shown": False,
                "project_selection_source": "saved_link",
                "project_candidate_count_loaded": 0,
                "project_multi_repo_match_count": 0,
                "saved_project_link_cleared": False,
                "project_repo_remote_changed": False,
            }

        monkeypatch.setattr(
            "vibe.acp.teleport._resolve_acp_teleport_project", resolve_project
        )
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]
        session.agent_loop.messages.append(
            LLMMessage(role=Role.user, content="ship it")
        )
        service = FakeTeleportService()
        _set_teleport_service(session.agent_loop, service)

        response = await _prompt(acp_agent_loop, session_id, "/teleport")

        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {
                "status": "completed",
                "url": "https://chat.example.com/code/1/2",
            },
        }
        assert service.project_ids == ["project-id"]
        assert telemetry_events[-1]["event_name"] == "vibe.teleport_completed"
        assert {
            "project_picker_shown": False,
            "project_selection_source": "saved_link",
            "project_candidate_count_loaded": 0,
            "project_multi_repo_match_count": 0,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }.items() <= telemetry_events[-1]["properties"].items()

    @pytest.mark.asyncio
    async def test_teleport_marks_tool_failed_when_project_resolution_fails(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        monkeypatch: pytest.MonkeyPatch,
        telemetry_events: list[dict],
    ) -> None:
        async def fail_project_resolution(_session: object) -> tuple[None, None]:
            raise VibeCodeProjectResolverError("Multiple projects match.")

        monkeypatch.setattr(
            "vibe.acp.teleport._resolve_acp_teleport_project", fail_project_resolution
        )
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]
        session.agent_loop.messages.append(
            LLMMessage(role=Role.user, content="ship it")
        )

        response = await _prompt(acp_agent_loop, session_id, "/teleport")

        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {"status": "failed"},
        }
        failed = _get_tool_updates(acp_agent_loop)[-1]
        assert failed.status == "failed"
        assert failed.raw_output == "Multiple projects match."
        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert {
            "stage": "git_check",
            "error_class": "VibeCodeProjectResolverError",
            "project_picker_shown": False,
            "project_selection_source": "cancelled",
            "project_candidate_count_loaded": 0,
            "project_multi_repo_match_count": 0,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }.items() <= telemetry_events[-1]["properties"].items()

    @pytest.mark.asyncio
    async def test_teleport_push_required_requests_permission(
        self, acp_agent_loop: VibeAcpAgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeTeleportService:
            response: TeleportPushResponseEvent | None

            def __init__(self) -> None:
                self.response = None

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, prompt: str, *, project_id: str | None = None, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                assert project_id is None
                yield TeleportCheckingGitEvent()
                response = yield TeleportPushRequiredEvent(
                    unpushed_count=2, branch_not_pushed=False
                )
                self.response = cast(TeleportPushResponseEvent, response)
                yield TeleportPushingEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/code/1/2")

        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]
        session.agent_loop.messages.append(
            LLMMessage(role=Role.user, content="ship it")
        )
        monkeypatch.setattr(
            "vibe.acp.teleport._resolve_acp_teleport_project",
            AsyncMock(return_value=(None, None)),
        )
        service = FakeTeleportService()
        _set_teleport_service(session.agent_loop, service)

        client = _get_client(acp_agent_loop)
        client.request_permission = AsyncMock(
            return_value=RequestPermissionResponse(
                outcome=AllowedOutcome(
                    outcome="selected", option_id=TELEPORT_PUSH_OPTION_ID
                )
            )
        )

        response = await _prompt(acp_agent_loop, session_id, "/teleport")

        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {
                "status": "completed",
                "url": "https://chat.example.com/code/1/2",
            },
        }
        assert service.response == TeleportPushResponseEvent(approved=True)
        request_permission = cast(AsyncMock, client.request_permission)
        request_permission.assert_awaited_once()
        await_args = request_permission.await_args
        assert await_args is not None
        kwargs = await_args.kwargs
        assert (
            kwargs["tool_call"].title
            == "You have 2 unpushed commits. Push to continue?"
        )
        assert kwargs["tool_call"].field_meta == {
            "tool_name": "teleport",
            "teleport": {
                "status": "push_required",
                "unpushedCount": 2,
                "branchNotPushed": False,
            },
        }
        assert [option.name for option in kwargs["options"]] == [
            "Push and continue",
            "Cancel",
        ]
        assert [update.field_meta for update in _get_tool_updates(acp_agent_loop)] == [
            {"tool_name": "teleport", "teleport": {"status": "starting"}},
            {"tool_name": "teleport", "teleport": {"status": "preparing_workspace"}},
            {
                "tool_name": "teleport",
                "teleport": {
                    "status": "push_required",
                    "unpushedCount": 2,
                    "branchNotPushed": False,
                },
            },
            {"tool_name": "teleport", "teleport": {"status": "syncing_remote"}},
            {
                "tool_name": "teleport",
                "teleport": {
                    "status": "completed",
                    "url": "https://chat.example.com/code/1/2",
                },
            },
        ]

    @pytest.mark.asyncio
    async def test_teleport_push_denied_marks_tool_call_failed(
        self, acp_agent_loop: VibeAcpAgentLoop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeTeleportService:
            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, prompt: str, *, project_id: str | None = None, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                assert project_id is None
                response = yield TeleportPushRequiredEvent()
                if (
                    not isinstance(response, TeleportPushResponseEvent)
                    or not response.approved
                ):
                    raise ServiceTeleportError(
                        "Teleport cancelled: changes not pushed."
                    )

        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]
        session.agent_loop.messages.append(
            LLMMessage(role=Role.user, content="ship it")
        )
        monkeypatch.setattr(
            "vibe.acp.teleport._resolve_acp_teleport_project",
            AsyncMock(return_value=(None, None)),
        )
        _set_teleport_service(session.agent_loop, FakeTeleportService())

        client = _get_client(acp_agent_loop)
        client.request_permission = AsyncMock(
            return_value=RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled")
            )
        )

        response = await _prompt(acp_agent_loop, session_id, "/teleport")

        assert response.field_meta == {
            "tool_name": "teleport",
            "teleport": {"status": "failed"},
        }
        failed = _get_tool_updates(acp_agent_loop)[-1]
        assert failed.status == "failed"
        assert failed.title == "Teleport failed"
        assert failed.raw_output == "Teleport cancelled: changes not pushed."
        assert failed.field_meta == {
            "tool_name": "teleport",
            "teleport": {"status": "failed"},
        }


class TestHandleReload:
    @pytest.mark.asyncio
    async def test_reload_calls_reload_with_initial_messages(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]

        with patch.object(
            session.agent_loop, "reload_with_initial_messages", new_callable=AsyncMock
        ) as mock_reload:
            response = await _prompt(acp_agent_loop, session_id, "/reload")
            assert response.stop_reason == "end_turn"
            mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_reload_notifies_commands_changed(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        session = acp_agent_loop.sessions[session_id]

        with patch.object(
            session.command_registry, "notify_changed", new_callable=AsyncMock
        ) as mock_notify:
            await _prompt(acp_agent_loop, session_id, "/reload")
            mock_notify.assert_called_once()


class TestCommandFallthrough:
    @pytest.mark.asyncio
    async def test_unknown_slash_command_reaches_agent(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/nonexistent")

        # The agent loop should have processed it (FakeBackend returns "Hi")
        assert response.stop_reason == "end_turn"
        texts = _get_message_texts(acp_agent_loop)
        # Should contain the LLM response, not a command reply
        assert any("Hi" in t for t in texts)

    @pytest.mark.asyncio
    async def test_regular_message_reaches_agent(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "Hello world")

        assert response.stop_reason == "end_turn"
        texts = _get_message_texts(acp_agent_loop)
        assert any("Hi" in t for t in texts)

    @pytest.mark.asyncio
    async def test_ampersand_message_reaches_agent(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "&fix from here")

        assert response.stop_reason == "end_turn"
        assert response.field_meta is None
        assert _get_tool_updates(acp_agent_loop) == []
        texts = _get_message_texts(acp_agent_loop)
        assert any("Hi" in t for t in texts)


class TestAvailableCommandsWithSkills:
    @pytest.mark.asyncio
    async def test_available_commands_are_alphabetical(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "alpha-skill", "First skill")

        await acp_agent_loop_with_skills.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        await _wait_for_available_commands(acp_agent_loop_with_skills)

        updates = _get_client(acp_agent_loop_with_skills)._session_updates
        available = [
            u for u in updates if isinstance(u.update, AvailableCommandsUpdate)
        ]
        cmd_names = [c.name for c in available[0].update.available_commands]

        assert cmd_names == sorted(cmd_names)

    @pytest.mark.asyncio
    async def test_skills_appear_in_available_commands(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "my-skill", "A useful skill")

        await acp_agent_loop_with_skills.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        await _wait_for_available_commands(acp_agent_loop_with_skills)

        updates = _get_client(acp_agent_loop_with_skills)._session_updates
        available = [
            u for u in updates if isinstance(u.update, AvailableCommandsUpdate)
        ]
        assert len(available) == 1

        cmd_names = [c.name for c in available[0].update.available_commands]
        assert "my-skill" in cmd_names
        # Built-in commands should also be present
        assert "help" in cmd_names

    @pytest.mark.asyncio
    async def test_non_invocable_skills_excluded_from_available_commands(
        self, acp_agent_loop_with_skills: VibeAcpAgentLoop, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "visible-skill", "Visible")
        create_skill(skills_dir, "hidden-skill", "Hidden", user_invocable=False)

        await acp_agent_loop_with_skills.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        await _wait_for_available_commands(acp_agent_loop_with_skills)

        updates = _get_client(acp_agent_loop_with_skills)._session_updates
        available = [
            u for u in updates if isinstance(u.update, AvailableCommandsUpdate)
        ]
        cmd_names = [c.name for c in available[0].update.available_commands]

        assert "visible-skill" in cmd_names
        assert "hidden-skill" not in cmd_names


class TestSlashCommandTelemetry:
    @pytest.mark.asyncio
    async def test_builtin_command_fires_telemetry(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        telemetry_events.clear()

        await _prompt(acp_agent_loop, session_id, "/help")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert len(slash_events) == 1
        assert slash_events[0]["properties"]["command"] == "help"
        assert slash_events[0]["properties"]["command_type"] == "builtin"

    @pytest.mark.asyncio
    async def test_skill_command_fires_telemetry(
        self,
        acp_agent_loop_with_skills: VibeAcpAgentLoop,
        skills_dir: Path,
        telemetry_events: list[dict],
    ) -> None:
        create_skill(skills_dir, "my-skill", "Does something")
        session_id = await _new_session_and_clear(acp_agent_loop_with_skills)
        telemetry_events.clear()

        await _prompt(acp_agent_loop_with_skills, session_id, "/my-skill")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert len(slash_events) == 1
        assert slash_events[0]["properties"]["command"] == "my-skill"
        assert slash_events[0]["properties"]["command_type"] == "skill"

    @pytest.mark.asyncio
    async def test_unknown_slash_command_does_not_fire_telemetry(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        telemetry_events.clear()

        await _prompt(acp_agent_loop, session_id, "/nonexistent")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert slash_events == []

    @pytest.mark.asyncio
    async def test_regular_message_does_not_fire_telemetry(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict]
    ) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        telemetry_events.clear()

        await _prompt(acp_agent_loop, session_id, "Hello world")

        slash_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.slash_command_used"
        ]
        assert slash_events == []


class TestCommandCaseInsensitivity:
    @pytest.mark.asyncio
    async def test_uppercase_command(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/HELP")

        assert response.stop_reason == "end_turn"
        content = _get_message_texts(acp_agent_loop)[0]
        assert "Available Commands" in content

    @pytest.mark.asyncio
    async def test_mixed_case_command(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_id = await _new_session_and_clear(acp_agent_loop)
        response = await _prompt(acp_agent_loop, session_id, "/Help")

        assert response.stop_reason == "end_turn"
        content = _get_message_texts(acp_agent_loop)[0]
        assert "Available Commands" in content
