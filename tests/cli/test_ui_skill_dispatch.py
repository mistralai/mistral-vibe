from __future__ import annotations

from pathlib import Path
import time

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from tests.skills.conftest import create_skill
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserMessage
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.types import Role

SKILL_BODY = "## Instructions\n\nDo the thing."


@pytest.fixture
def vibe_app_with_skills(tmp_path: Path) -> VibeApp:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    create_skill(skills_dir, "my-skill", body=SKILL_BODY)
    return build_test_vibe_app(config=build_test_vibe_config(skill_paths=[skills_dir]))


async def _wait_for_user_message_containing(
    vibe_app: VibeApp, pilot, text: str, timeout: float = 1.0
) -> UserMessage:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in vibe_app.query(UserMessage):
            if text in message._content:
                return message
        await pilot.pause(0.05)
    raise TimeoutError(
        f"UserMessage containing {text!r} did not appear within {timeout}s"
    )


async def _wait_for_error_message_containing(
    vibe_app: VibeApp, pilot, text: str, timeout: float = 1.0
) -> ErrorMessage:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for error in vibe_app.query(ErrorMessage):
            if text in str(error._error):
                return error
        await pilot.pause(0.05)
    raise TimeoutError(
        f"ErrorMessage containing {text!r} did not appear within {timeout}s"
    )


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


@pytest.mark.asyncio
async def test_skill_without_args_displays_literal_command(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
        await pilot.pause(0.1)

        message = await _wait_for_user_message_containing(
            vibe_app_with_skills, pilot, "/my-skill"
        )
        assert message._content == "/my-skill"
        assert "Do the thing." not in message._content


@pytest.mark.asyncio
async def test_skill_with_args_displays_literal_command_with_args(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill foo bar"))
        await pilot.pause(0.1)

        message = await _wait_for_user_message_containing(
            vibe_app_with_skills, pilot, "/my-skill foo bar"
        )
        assert message._content == "/my-skill foo bar"
        assert "Do the thing." not in message._content


@pytest.mark.asyncio
async def test_unknown_skill_falls_through_to_agent(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/nonexistent-skill"))
        await pilot.pause(0.2)

        skill_errors = [
            e
            for e in vibe_app_with_skills.query(ErrorMessage)
            if "skill" in str(getattr(e, "_error", "")).lower()
        ]
        assert not skill_errors


@pytest.mark.asyncio
async def test_bare_slash_falls_through(vibe_app_with_skills: VibeApp) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/"))
        await pilot.pause(0.2)

        assert not any(
            "Do the thing." in m._content
            for m in vibe_app_with_skills.query(UserMessage)
        )


@pytest.mark.asyncio
async def test_skill_without_args_does_not_add_extra_text(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
        await pilot.pause(0.1)

        message = await _wait_for_user_message_containing(
            vibe_app_with_skills, pilot, "/my-skill"
        )
        assert message._content == "/my-skill"


@pytest.mark.asyncio
async def test_idle_skill_fires_telemetry(
    vibe_app_with_skills: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        events: list[tuple[str, str]] = []
        monkeypatch.setattr(
            vibe_app_with_skills.agent_loop.telemetry_client,
            "send_slash_command_used",
            lambda name, kind: events.append((name, kind)),
        )

        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill foo"))
        await pilot.pause(0.1)

        assert events == [("my-skill", "skill")]


@pytest.mark.asyncio
async def test_popped_queued_skill_does_not_fire_telemetry(
    vibe_app_with_skills: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        events: list[tuple[str, str]] = []
        monkeypatch.setattr(
            vibe_app_with_skills.agent_loop.telemetry_client,
            "send_slash_command_used",
            lambda name, kind: events.append((name, kind)),
        )

        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        vibe_app_with_skills._agent_running = True
        try:
            chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
            await pilot.pause(0.1)
            assert len(vibe_app_with_skills._input_queue) == 1

            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert len(vibe_app_with_skills._input_queue) == 0
            assert events == []
        finally:
            vibe_app_with_skills._agent_running = False


@pytest.mark.asyncio
async def test_queued_head_skill_injects_skill_tool_message(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        vibe_app_with_skills._agent_running = True
        try:
            chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
            chat_input.post_message(ChatInputContainer.Submitted("follow-up prompt"))
            await pilot.pause(0.1)
            assert len(vibe_app_with_skills._input_queue) == 2
        finally:
            vibe_app_with_skills._agent_running = False

        vibe_app_with_skills._queue.start_drain_if_needed()

        assert await _wait_until(
            pilot,
            lambda: (
                len(vibe_app_with_skills._input_queue) == 0
                and vibe_app_with_skills._agent_task is None
                and any(
                    widget._tool_name == "skill"
                    for widget in vibe_app_with_skills.query(ToolCallMessage)
                )
                and any(
                    widget.tool_name == "skill"
                    for widget in vibe_app_with_skills.query(ToolResultMessage)
                )
            ),
            timeout=5.0,
        )

        assert any(
            message.role == Role.tool
            and message.name == "skill"
            and '<skill_content name="my-skill">' in (message.content or "")
            for message in vibe_app_with_skills.agent_loop.messages
        )


@pytest.mark.asyncio
async def test_skill_prompt_flushed_before_bash_injects_skill_tool_message(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        vibe_app_with_skills._agent_running = True
        try:
            chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
            chat_input.post_message(ChatInputContainer.Submitted("!echo queued"))
            await pilot.pause(0.1)
            assert len(vibe_app_with_skills._input_queue) == 2
        finally:
            vibe_app_with_skills._agent_running = False

        vibe_app_with_skills._queue.start_drain_if_needed()

        assert await _wait_until(
            pilot,
            lambda: (
                len(vibe_app_with_skills._input_queue) == 0
                and vibe_app_with_skills._agent_task is None
                and vibe_app_with_skills._bash_task is None
                and any(
                    widget._tool_name == "skill"
                    for widget in vibe_app_with_skills.query(ToolCallMessage)
                )
                and any(
                    widget.tool_name == "skill"
                    for widget in vibe_app_with_skills.query(ToolResultMessage)
                )
            ),
            timeout=5.0,
        )

        assert any(
            message.role == Role.tool
            and message.name == "skill"
            and '<skill_content name="my-skill">' in (message.content or "")
            for message in vibe_app_with_skills.agent_loop.messages
        )
