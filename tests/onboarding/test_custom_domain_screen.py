from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Input

from vibe.setup.onboarding.screens.custom_domain import CustomDomainScreen
from vibe.setup.onboarding.widgets.no_markup_static import NoMarkupStatic


class _Harness(App[None]):
    def __init__(
        self,
        *,
        configured_custom_domain: str | None = None,
        configured_custom_api_base: str | None = None,
        pinned_console_url: str | None = None,
    ) -> None:
        super().__init__()
        self._configured_custom_domain = configured_custom_domain
        self._configured_custom_api_base = configured_custom_api_base
        self._pinned_console_url = pinned_console_url

    @property
    def configured_custom_domain(self) -> str | None:
        return self._configured_custom_domain

    @property
    def configured_custom_api_base(self) -> str | None:
        return self._configured_custom_api_base

    @property
    def pinned_console_url(self) -> str | None:
        return self._pinned_console_url

    def get_default_screen(self) -> CustomDomainScreen:
        return CustomDomainScreen()


@pytest.mark.asyncio
async def test_screen_has_optional_api_base_input() -> None:
    async with _Harness().run_test() as pilot:
        api_base = pilot.app.query_one("#api-base", Input)
        assert api_base.value == ""


@pytest.mark.asyncio
async def test_domain_input_starts_empty_with_placeholder() -> None:
    async with _Harness().run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        assert domain.value == ""
        assert domain.placeholder == "console.mistral.ai"


@pytest.mark.asyncio
async def test_api_base_input_seeds_from_configured_split_horizon_base() -> None:
    api_base = "https://connector.internal.example:443/api"
    async with _Harness(
        configured_custom_domain="https://console.internal.example",
        configured_custom_api_base=api_base,
    ).run_test() as pilot:
        api_base_input = pilot.app.query_one("#api-base", Input)
        assert api_base_input.value == api_base


@pytest.mark.asyncio
async def test_invalid_api_base_marks_box_invalid() -> None:
    async with _Harness().run_test() as pilot:
        api_base_input = pilot.app.query_one("#api-base", Input)
        api_base_input.value = "ftp://example.com"
        await pilot.pause()

        box = pilot.app.query_one("#api-base-box")
        feedback = pilot.app.query_one("#feedback", NoMarkupStatic)
        assert box.has_class("invalid")
        assert feedback.has_class("error")


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
async def test_pinned_console_url_prefills_and_locks_domain_input() -> None:
    async with _Harness(
        pinned_console_url="https://console.acme.internal",
        configured_custom_api_base="https://stale.connector.example/api",
    ).run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        assert domain.value == "https://console.acme.internal"
        assert domain.disabled

        # The API base is locked to the console's derived default too: the
        # input is disabled and never seeded from stale config.
        api_base = pilot.app.query_one("#api-base", Input)
        assert api_base.disabled
        assert api_base.value == ""
        assert not pilot.app.query_one("#api-base-box").display

        title = pilot.app.query_one("#custom-domain-title", NoMarkupStatic)
        subtitle = pilot.app.query_one("#custom-domain-subtitle", NoMarkupStatic)
        assert str(title.content) == "Sign in to your console"
        assert (
            str(subtitle.content)
            == "Your administrator has configured Vibe to sign in to this console."
        )


@pytest.mark.asyncio
async def test_unpinned_domain_input_stays_editable() -> None:
    async with _Harness(
        configured_custom_api_base="https://connector.internal.example/api"
    ).run_test() as pilot:
        domain = pilot.app.query_one("#domain", Input)
        assert not domain.disabled

        api_base = pilot.app.query_one("#api-base", Input)
        assert not api_base.disabled
        assert api_base.value == "https://connector.internal.example/api"
        assert pilot.app.query_one("#api-base-box").display

        title = pilot.app.query_one("#custom-domain-title", NoMarkupStatic)
        subtitle = pilot.app.query_one("#custom-domain-subtitle", NoMarkupStatic)
        assert str(title.content) == "Use a custom domain"
        assert str(subtitle.content) == "Point Vibe at a Mistral-compatible deployment."


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
