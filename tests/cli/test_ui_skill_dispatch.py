from __future__ import annotations

from pathlib import Path
import time

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from tests.skills.conftest import create_skill
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserMessage

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
            if text in error._error:
                return error
        await pilot.pause(0.05)
    raise TimeoutError(
        f"ErrorMessage containing {text!r} did not appear within {timeout}s"
    )


@pytest.mark.asyncio
async def test_skill_without_args_sends_skill_content(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
        await pilot.pause(0.1)

        message = await _wait_for_user_message_containing(
            vibe_app_with_skills, pilot, "Do the thing."
        )
        assert "Do the thing." in message._content


@pytest.mark.asyncio
async def test_skill_with_args_prepends_invocation_line(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill foo bar"))
        await pilot.pause(0.1)

        message = await _wait_for_user_message_containing(
            vibe_app_with_skills, pilot, "Do the thing."
        )
        assert "/my-skill foo bar" in message._content
        assert "Do the thing." in message._content


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
async def test_skill_without_args_does_not_prepend_invocation_line(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
        await pilot.pause(0.1)

        message = await _wait_for_user_message_containing(
            vibe_app_with_skills, pilot, "Do the thing."
        )
        assert "/my-skill" not in message._content


@pytest.mark.asyncio
async def test_skill_with_missing_file_shows_error(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_dir = create_skill(skills_dir, "my-skill", body=SKILL_BODY)
    vibe_app = build_test_vibe_app(
        config=build_test_vibe_config(skill_paths=[skills_dir])
    )
    (skill_dir / "SKILL.md").unlink()

    async with vibe_app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))

        error = await _wait_for_error_message_containing(
            vibe_app, pilot, "Failed to read skill file"
        )
        assert "Failed to read skill file" in error._error
