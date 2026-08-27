from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer, ChatTextArea
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup

SKILLS: list[tuple[str, str]] = [
    ("/polco", "Make user feel good"),
    ("/review", "Review code changes"),
]


NESTED_SKILLS: list[tuple[str, str]] = [
    ("/implement", "Implement a change"),
    ("/implement-plan", "Implement an existing plan"),
]


def app_with_skills() -> VibeApp:
    app = build_test_vibe_app()
    app._get_skill_entries = lambda: list(SKILLS)
    return app


def app_with_nested_skills() -> VibeApp:
    app = build_test_vibe_app()
    app._get_skill_entries = lambda: list(NESTED_SKILLS)
    return app


@pytest.mark.asyncio
async def test_slash_mid_prompt_shows_ghost_suggestion() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /pol")
        await pilot.pause(0.1)

        assert text_area.suggestion == "co"
        assert app.query_one(CompletionPopup).styles.display == "none"


@pytest.mark.asyncio
async def test_tab_accepts_ghost_suggestion() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /pol")
        await pilot.pause(0.1)
        await pilot.press("tab")
        await pilot.pause(0.1)

        assert chat_input.value == "run /polco"
        assert text_area.suggestion == ""


@pytest.mark.asyncio
async def test_tab_accept_surfaces_deeper_ghost() -> None:
    app = app_with_nested_skills()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /impl")
        await pilot.pause(0.1)
        assert text_area.suggestion == "ement"

        await pilot.press("tab")
        await pilot.pause(0.1)

        # Tab accepts /implement and, like right-arrow, keeps offering the
        # deeper /implement-plan completion.
        assert chat_input.value == "run /implement"
        assert text_area.suggestion == "-plan"


@pytest.mark.asyncio
async def test_ghost_clears_when_token_no_longer_matches() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /pol")
        await pilot.pause(0.1)
        assert text_area.suggestion == "co"

        await pilot.press("z")
        await pilot.pause(0.1)

        assert text_area.suggestion == ""


@pytest.mark.asyncio
async def test_mouse_click_off_token_clears_ghost_suggestion() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /pol")
        await pilot.pause(0.1)
        assert text_area.suggestion == "co"

        # Click at the very start: the caret leaves the /pol token, so the ghost
        # must clear rather than linger at the new position.
        await pilot.click(ChatTextArea, offset=(0, 0))
        await pilot.pause(0.1)

        assert text_area.suggestion == ""


@pytest.mark.asyncio
async def test_arrow_key_clears_ghost_suggestion() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /pol")
        await pilot.pause(0.1)
        assert text_area.suggestion == "co"

        await pilot.press("left")
        await pilot.pause(0.1)

        assert text_area.suggestion == ""


@pytest.mark.asyncio
async def test_word_nav_key_clears_ghost_suggestion() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"run /pol")
        await pilot.pause(0.1)
        assert text_area.suggestion == "co"

        # Word navigation moves the caret off the token; the ghost must clear
        # even though the key is not in any hard-coded dismiss list.
        await pilot.press("alt+left")
        await pilot.pause(0.1)

        assert text_area.suggestion == ""


@pytest.mark.asyncio
async def test_mouse_click_keeps_slash_popup() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"/pol")
        await pilot.pause(0.1)
        assert app.query_one(CompletionPopup).styles.display == "block"

        # A click re-evaluates completions in place; the slash popup stays.
        await pilot.click(ChatTextArea, offset=(0, 0))
        await pilot.pause(0.1)

        assert app.query_one(CompletionPopup).styles.display == "block"


@pytest.mark.asyncio
async def test_escape_clears_leading_slash_input_without_suggestions() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"/zzzzz")
        await pilot.pause(0.1)
        # No command/skill matches, so the popup is hidden; Escape must still
        # clear the mistyped slash input.
        assert app.query_one(CompletionPopup).styles.display == "none"

        await pilot.press("escape")
        await pilot.pause(0.1)

        assert chat_input.value == ""


@pytest.mark.asyncio
async def test_leading_slash_uses_popup_not_ghost() -> None:
    app = app_with_skills()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press(*"/pol")
        await pilot.pause(0.1)

        assert text_area.suggestion == ""
        assert app.query_one(CompletionPopup).styles.display == "block"
