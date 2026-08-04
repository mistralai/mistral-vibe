from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Input

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.setup.onboarding.screens.custom_domain import CustomDomainScreen


class _Harness(App[None]):
    @property
    def configured_custom_domain(self) -> str | None:
        return None

    def get_default_screen(self) -> CustomDomainScreen:
        return CustomDomainScreen()


@pytest.mark.asyncio
async def test_screen_has_no_auth_api_base_input() -> None:
    async with _Harness().run_test() as pilot:
        assert not pilot.app.query("#auth-api-base")


@pytest.mark.asyncio
async def test_domain_input_starts_empty_with_placeholder() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        assert domain.value == ""
        assert domain.placeholder == "console.mistral.ai"


@pytest.mark.asyncio
async def test_typing_domain_keeps_value_as_typed() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press(*"example.com")
        await pilot.pause()

        assert domain.value == "example.com"


@pytest.mark.asyncio
async def test_valid_domain_marks_box_valid_and_feedback_success() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()

        box = pilot.app.query_one("#domain-box")
        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        assert box.has_class("valid")
        assert not box.has_class("invalid")
        assert feedback.has_class("success")
        assert not feedback.has_class("error")


@pytest.mark.asyncio
async def test_mistral_private_cloud_domain_shows_warning() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press(*"console.123.mistral.ai")
        await pilot.pause()

        box = pilot.app.query_one("#domain-box")
        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        assert box.has_class("warning")
        assert not box.has_class("valid")
        assert feedback.has_class("warning")


@pytest.mark.asyncio
async def test_default_mistral_domain_shows_no_warning() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press(*"console.mistral.ai")
        await pilot.pause()

        box = pilot.app.query_one("#domain-box")
        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        assert box.has_class("valid")
        assert not box.has_class("warning")
        assert feedback.has_class("success")
        assert not feedback.has_class("warning")


@pytest.mark.asyncio
async def test_cleared_domain_marks_box_invalid_and_feedback_error() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        await pilot.press("backspace")
        await pilot.pause()

        box = pilot.app.query_one("#domain-box")
        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        assert box.has_class("invalid")
        assert not box.has_class("valid")
        assert feedback.has_class("error")
        assert not feedback.has_class("success")


@pytest.mark.asyncio
async def test_reset_feedback_clears_all_state_classes_and_text() -> None:
    async with _Harness().run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, CustomDomainScreen)
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press(*"console.123.mistral.ai")
        await pilot.pause()

        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        box = pilot.app.query_one("#domain-box")
        assert feedback.has_class("warning")
        assert box.has_class("warning")

        screen._reset_feedback(feedback, box)

        assert str(feedback.content) == ""
        for state in ("error", "success", "warning"):
            assert not feedback.has_class(state)
        for state in ("valid", "invalid", "warning"):
            assert not box.has_class(state)


@pytest.mark.asyncio
async def test_reset_input_clears_stale_warning_state() -> None:
    async with _Harness().run_test() as pilot:
        screen = pilot.app.screen
        assert isinstance(screen, CustomDomainScreen)
        domain = pilot.app.query_one("#domain", Input)
        domain.focus()
        await pilot.pause()

        await pilot.press(*"console.123.mistral.ai")
        await pilot.pause()

        box = pilot.app.query_one("#domain-box")
        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        assert box.has_class("warning")
        assert feedback.has_class("warning")

        screen._reset_input()

        assert domain.value == ""
        for state in ("valid", "invalid", "warning"):
            assert not box.has_class(state)
        for state in ("error", "success", "warning"):
            assert not feedback.has_class(state)
