from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from textual.content import Content
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from tests.setup.onboarding._test_helpers import (
    BROWSER_AUTH_API_URL,
    CONSOLE_URL,
    build_browser_onboarding_app as _build_browser_onboarding_app,
    build_onboarding_config as _build_onboarding_config,
    saved_env_contents as _saved_env_contents,
    show_browser_sign_in as _show_browser_sign_in,
    wait_for as _wait_for,
)
from tests.stubs.fake_browser_sign_in_gateway import (
    BrowserSignInPollScript,
    FakeBrowserSignInGateway,
    build_browser_sign_in_service_factory,
    build_completed_poll_script,
    build_expired_poll_script,
    build_poll_failed_script,
    build_sign_in_process,
)
from vibe.cli.textual_ui.shortcut_hints import SHORTCUT_STYLE
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend
from vibe.setup.auth import (
    BrowserSignInEvent,
    BrowserSignInPollResult,
    BrowserSignInService,
    BrowserSignInStatus,
    BrowserSignInStatusChanged,
)
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.context import OnboardingContext
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen
from vibe.setup.onboarding.screens.browser_sign_in import (
    SIGN_IN_URL_HELP_PREFIX,
    BrowserSignInScreen,
    BrowserSignInStep,
    BrowserSignInStepWidgets,
    BrowserSignInViewState,
)

TEST_NOW = datetime(2026, 3, 16, tzinfo=UTC)


def _expected_browser_sign_in_url(process_id: str = "process-1") -> str:
    return build_sign_in_process(TEST_NOW, process_id=process_id).sign_in_url


def _browser_sign_in_step_cards(screen: Screen) -> list[Widget]:
    return list(screen.query(".browser-sign-in-step"))


def _active_browser_sign_in_step_card(screen: Screen) -> Widget:
    active_cards = [
        card for card in _browser_sign_in_step_cards(screen) if card.has_class("active")
    ]
    if len(active_cards) != 1:
        msg = "Expected exactly one active browser sign-in step."
        raise AssertionError(msg)
    return active_cards[0]


def _browser_sign_in_step_text(card: Widget) -> str:
    title = card.query_one(".browser-sign-in-step-title", NoMarkupStatic)
    detail = card.query_one(".browser-sign-in-step-detail", NoMarkupStatic)
    return f"{title.render()}\n{detail.render()}"


def _browser_sign_in_hint(screen: Screen) -> str:
    return str(screen.query_one("#browser-sign-in-hint", NoMarkupStatic).render())


def _assert_browser_sign_in_shortcuts_styled(screen: Screen) -> None:
    text = screen.query_one("#browser-sign-in-hint", NoMarkupStatic).render()
    assert isinstance(text, Content)
    assert any(span.style == SHORTCUT_STYLE for span in text.spans)


def _browser_sign_in_url_text(screen: Screen) -> str:
    return str(screen.query_one("#browser-sign-in-url", Static).render())


def _blocked_sleep(blocker: asyncio.Event) -> Callable[[float], Awaitable[None]]:
    async def wait(_: float) -> None:
        await blocker.wait()

    return wait


def _assert_browser_sign_in_waiting(
    screen: Screen, gateway: FakeBrowserSignInGateway
) -> BrowserSignInScreen:
    assert isinstance(screen, BrowserSignInScreen)
    assert screen.state.variant == "pending"
    assert screen.state.running is True
    assert gateway.poll_calls == 1
    return screen


@dataclass(frozen=True)
class BrowserSignInCleanupProbe:
    blocker: asyncio.Event = field(default_factory=asyncio.Event)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)


def _build_unexpected_browser_sign_in_service_factory(
    results: list[str | Exception],
    *,
    cleanup_probe: BrowserSignInCleanupProbe | None = None,
) -> Callable[[], BrowserSignInService]:
    remaining_results = list(results)

    class FakeBrowserSignInService:
        def __init__(self, result: str | Exception) -> None:
            self._result = result

        async def authenticate(
            self, event_callback: Callable[[BrowserSignInEvent], None] | None = None
        ) -> str:
            if isinstance(self._result, Exception):
                raise self._result
            if event_callback is not None:
                event_callback(
                    BrowserSignInStatusChanged(status=BrowserSignInStatus.COMPLETED)
                )
            return self._result

        async def aclose(self) -> None:
            if cleanup_probe is None:
                return
            try:
                cleanup_probe.started.set()
                await cleanup_probe.blocker.wait()
            except asyncio.CancelledError:
                cleanup_probe.cancelled.set()
                raise
            finally:
                cleanup_probe.finished.set()

    def build_service() -> BrowserSignInService:
        if not remaining_results:
            msg = (
                "Unexpected browser sign-in service factory requires scripted results."
            )
            raise AssertionError(msg)
        return cast(
            BrowserSignInService, FakeBrowserSignInService(remaining_results.pop(0))
        )

    return build_service


def test_browser_sign_in_gradient_animation_skips_relayout() -> None:
    provider = _build_onboarding_config(
        browser_auth_base_url=CONSOLE_URL,
        browser_auth_api_base_url=BROWSER_AUTH_API_URL,
    ).providers[0]
    screen = BrowserSignInScreen(provider, MagicMock(), copy_sign_in_url=MagicMock())
    screen.state = BrowserSignInViewState(
        step=BrowserSignInStep.CONFIRM,
        message="Waiting for authentication...",
        variant="pending",
        running=True,
    )
    detail = MagicMock(spec=NoMarkupStatic)
    step_widgets = BrowserSignInStepWidgets(
        marker=MagicMock(spec=NoMarkupStatic),
        card=MagicMock(),
        title=MagicMock(spec=NoMarkupStatic),
        detail=detail,
    )
    screen._step_widgets = [step_widgets, step_widgets]

    screen._animate_gradient()

    detail.update.assert_called_once()
    assert detail.update.call_args.kwargs == {"layout": False}


@pytest.mark.asyncio
async def test_ui_does_not_show_sign_in_page_ready_before_attempt_starts() -> None:
    authenticate_started = asyncio.Event()
    finish_authenticate = asyncio.Event()
    keep_authenticate_running = asyncio.Event()
    copied_urls: list[str] = []

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    class FakeDelayedBrowserSignInService:
        async def authenticate(
            self, event_callback: Callable[[BrowserSignInEvent], None] | None = None
        ) -> str:
            authenticate_started.set()
            await finish_authenticate.wait()
            if event_callback is not None:
                event_callback(
                    BrowserSignInStatusChanged(
                        status=BrowserSignInStatus.OPENING_BROWSER
                    )
                )
            await keep_authenticate_running.wait()
            return "sk-never-reached"

        async def aclose(self) -> None:
            return None

    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=lambda: cast(
            BrowserSignInService, FakeDelayedBrowserSignInService()
        ),
        copy_sign_in_url=copy_sign_in_url,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(authenticate_started.is_set, pilot)

        active_step = _active_browser_sign_in_step_card(app.screen)
        active_step_text = _browser_sign_in_step_text(active_step)
        assert "Open browser" in active_step_text
        assert "Getting things ready..." in active_step_text
        assert "Sign-in page ready" not in active_step_text
        assert _browser_sign_in_url_text(app.screen) == ""
        await pilot.press("c")
        assert copied_urls == []

        finish_authenticate.set()
        await _wait_for(
            lambda: (
                "Opening your browser..."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )


@pytest.mark.asyncio
async def test_ui_shows_browser_sign_in_url_copy_prompt_without_raw_url() -> None:
    blocker = asyncio.Event()

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()], sleep=_blocked_sleep(blocker)
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        url_text = _browser_sign_in_url_text(app.screen)
        assert "If your browser did not open, copy this URL (press c)" in url_text
        url_render = app.screen.query_one("#browser-sign-in-url", Static).render()
        assert isinstance(url_render, Content)
        assert any(span.style == SHORTCUT_STYLE for span in url_render.spans)
        assert "process-1" not in url_text
        assert _browser_sign_in_hint(app.screen) == (
            "Press m to enter API key manually - Esc to cancel"
        )
        _assert_browser_sign_in_shortcuts_styled(app.screen)


@pytest.mark.asyncio
async def test_ui_delays_browser_sign_in_url_help() -> None:
    blocker = asyncio.Event()

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()], sleep=_blocked_sleep(blocker)
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        browser_sign_in_url_help_delay=0.3,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Waiting for authentication..."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        assert _browser_sign_in_url_text(app.screen) == ""

        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        assert "process-1" not in _browser_sign_in_url_text(app.screen)


@pytest.mark.asyncio
async def test_ui_copies_browser_sign_in_url() -> None:
    blocker = asyncio.Event()
    copied_urls: list[str] = []

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()], sleep=_blocked_sleep(blocker)
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        copy_sign_in_url=copy_sign_in_url,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        await pilot.press("c")
        assert "process-1" not in _browser_sign_in_url_text(app.screen)
        _assert_browser_sign_in_waiting(app.screen, gateway)

    assert copied_urls == [_expected_browser_sign_in_url()]


@pytest.mark.asyncio
async def test_ui_copies_browser_sign_in_url_when_help_text_is_clicked() -> None:
    blocker = asyncio.Event()
    copied_urls: list[str] = []

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()], sleep=_blocked_sleep(blocker)
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        copy_sign_in_url=copy_sign_in_url,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        url_widget = app.screen.query_one("#browser-sign-in-url", Static)
        link_x = url_widget.styles.padding.left + len(SIGN_IN_URL_HELP_PREFIX) + 1
        await pilot.click(url_widget, offset=(link_x, 0))

    assert copied_urls == [_expected_browser_sign_in_url()]


@pytest.mark.asyncio
async def test_ui_reveals_browser_sign_in_url_when_copy_fails() -> None:
    blocker = asyncio.Event()

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()], sleep=_blocked_sleep(blocker)
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        copy_sign_in_url=lambda _: False,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        assert "process-1" not in _browser_sign_in_url_text(app.screen)

        await pilot.press("c")

        await _wait_for(
            lambda: "process-1" in _browser_sign_in_url_text(app.screen), pilot
        )
        url_text = _browser_sign_in_url_text(app.screen)
        assert "Copy failed. Open this URL manually:" in url_text
        _assert_browser_sign_in_waiting(app.screen, gateway)


@pytest.mark.asyncio
async def test_ui_completes_headless_browser_sign_in_after_copying_url() -> None:
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    copied_urls: list[str] = []

    async def wait_before_poll_result(poll_number: int) -> None:
        if poll_number != 1:
            return
        poll_started.set()
        await release_poll.wait()

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()],
        open_browser=lambda _: False,
        raise_on_browser_open_failure=False,
        wait_before_poll_result=wait_before_poll_result,
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        copy_sign_in_url=copy_sign_in_url,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(poll_started.is_set, pilot)
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        await pilot.press("c")
        _assert_browser_sign_in_waiting(app.screen, gateway)
        step_cards = _browser_sign_in_step_cards(app.screen)
        assert "Sign-in page ready" in _browser_sign_in_step_text(step_cards[0])
        assert "Browser opened" not in _browser_sign_in_step_text(step_cards[0])

        release_poll.set()
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert copied_urls == [_expected_browser_sign_in_url()]
    assert gateway.poll_calls == 2
    assert len(gateway.exchange_requests) == 1
    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("poll_script", "expected_error"),
    [
        (
            (
                BrowserSignInPollResult(status="pending"),
                BrowserSignInPollResult(status="expired"),
            ),
            "Browser sign-in expired.",
        ),
        (
            (
                BrowserSignInPollResult(status="pending"),
                BrowserSignInPollResult(status="denied"),
            ),
            "Browser sign-in was denied.",
        ),
    ],
    ids=["expired", "denied"],
)
async def test_ui_preserves_terminal_error_after_copying_headless_sign_in_url(
    poll_script: BrowserSignInPollScript, expected_error: str
) -> None:
    poll_started = asyncio.Event()
    release_poll = asyncio.Event()
    copied_urls: list[str] = []

    async def wait_before_poll_result(poll_number: int) -> None:
        if poll_number != 1:
            return
        poll_started.set()
        await release_poll.wait()

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[poll_script],
        open_browser=lambda _: False,
        raise_on_browser_open_failure=False,
        wait_before_poll_result=wait_before_poll_result,
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        copy_sign_in_url=lambda url: copied_urls.append(url) or True,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(poll_started.is_set, pilot)
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )
        await pilot.press("c")
        screen = _assert_browser_sign_in_waiting(app.screen, gateway)

        release_poll.set()
        await _wait_for(
            lambda: (
                expected_error
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )

        assert screen.state.variant == "error"
        assert screen.state.running is False

    assert copied_urls == [_expected_browser_sign_in_url()]
    assert gateway.poll_calls == 2
    assert gateway.exchange_requests == []


@pytest.mark.asyncio
async def test_ui_retry_hides_old_sign_in_url_and_uses_fresh_attempt_url() -> None:
    blocker = asyncio.Event()
    copied_urls: list[str] = []

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[
            build_expired_poll_script(),
            build_completed_poll_script(process_id="process-2"),
        ],
        sleep=_blocked_sleep(blocker),
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        copy_sign_in_url=copy_sign_in_url,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "expired"
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        await _wait_for(
            lambda: "copy this URL" in _browser_sign_in_url_text(app.screen), pilot
        )

        await pilot.press("r")
        await _wait_for(
            lambda: (
                "Waiting for authentication..."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
                and "copy this URL" in _browser_sign_in_url_text(app.screen)
            ),
            pilot,
        )
        assert "process-2" not in _browser_sign_in_url_text(app.screen)
        assert "process-1" not in _browser_sign_in_url_text(app.screen)
        assert _browser_sign_in_hint(app.screen) == (
            "Press m to enter API key manually - Esc to cancel"
        )
        _assert_browser_sign_in_shortcuts_styled(app.screen)
        await pilot.press("c")

    assert copied_urls == [_expected_browser_sign_in_url(process_id="process-2")]


@pytest.mark.asyncio
async def test_ui_completes_browser_sign_in_and_retries_after_failure() -> None:
    gateway, browser_sign_in_service_factory, created_services = (
        build_browser_sign_in_service_factory(
            poll_scripts=[
                build_expired_poll_script(),
                build_completed_poll_script(process_id="process-2"),
            ]
        )
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "expired"
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        await pilot.press("r")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert gateway.process_number == 2
    assert len(created_services) == 2
    assert created_services[0] is not created_services[1]
    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_preserves_completed_browser_sign_in_during_success_delay() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()]
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        browser_sign_in_success_delay=0.5,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Sign-in complete"
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        assert isinstance(app.screen, BrowserSignInScreen)
        assert app.screen.state.variant == "success"
        hint = str(app.screen.query_one("#browser-sign-in-hint").render())
        assert "Finishing setup..." in hint
        assert "Press m to enter API key manually - Esc to cancel" not in hint
        assert app.return_value is None
        assert "sk-browser-onboarding-test-key" in _saved_env_contents()
        await pilot.press("m", "escape")
        assert isinstance(app.screen, BrowserSignInScreen)
        assert app.return_value is None
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_skips_success_delay_when_browser_api_key_cannot_be_persisted() -> (
    None
):
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()]
    )
    provider = ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="BAD=NAME",
        browser_auth_base_url=CONSOLE_URL,
        browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        backend=Backend.MISTRAL,
    )
    app = OnboardingApp(
        config=OnboardingContext(provider=provider),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        browser_sign_in_success_delay=2.0,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=0.5)

    assert app.return_value == "env_var_error:BAD=NAME"


@pytest.mark.asyncio
async def test_ui_browser_sign_in_falls_back_to_mistral_env_var_when_missing() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()]
    )
    app = OnboardingApp(
        config=_build_onboarding_config(
            provider_name="custom-mistral",
            api_key_env_var="",
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    env_contents = _saved_env_contents()
    assert "MISTRAL_API_KEY" in env_contents
    assert "sk-browser-onboarding-test-key" in env_contents


@pytest.mark.asyncio
async def test_ui_shows_human_message_when_polling_fails() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_poll_failed_script()]
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "We couldn't complete sign-in. Please try again."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )


@pytest.mark.asyncio
async def test_ui_shows_retryable_error_when_browser_sign_in_fails_unexpectedly() -> (
    None
):
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory([
            RuntimeError("boom")
        ])
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Something went wrong during browser sign-in. Please try again."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )

        assert isinstance(app.screen, BrowserSignInScreen)
        assert app.screen.state.variant == "error"
        assert _active_browser_sign_in_step_card(app.screen).has_class("active")
        assert (
            "Press r to retry - Press m to enter API key manually - Esc to cancel"
            in str(app.screen.query_one("#browser-sign-in-hint").render())
        )
        _assert_browser_sign_in_shortcuts_styled(app.screen)
        assert app.return_value is None


@pytest.mark.asyncio
async def test_ui_retries_after_unexpected_browser_sign_in_failure() -> None:
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory([
            RuntimeError("boom"),
            "sk-browser-onboarding-test-key",
        ])
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Something went wrong during browser sign-in. Please try again."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        await pilot.press("r")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_waits_for_browser_sign_in_cleanup_before_retrying() -> None:
    cleanup_probe = BrowserSignInCleanupProbe()
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory(
            [RuntimeError("boom"), "sk-browser-onboarding-test-key"],
            cleanup_probe=cleanup_probe,
        )
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(cleanup_probe.started.is_set, pilot)

        hint_widget = app.screen.query_one("#browser-sign-in-hint")
        await _wait_for(
            lambda: (
                "Getting things ready..."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        await _wait_for(
            lambda: (
                "Press m to enter API key manually - Esc to cancel"
                in str(hint_widget.render())
            ),
            pilot,
        )
        _assert_browser_sign_in_shortcuts_styled(app.screen)

        await pilot.press("r")
        await _wait_for(
            lambda: (
                "Getting things ready..."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        await _wait_for(
            lambda: (
                "Press m to enter API key manually - Esc to cancel"
                in str(hint_widget.render())
            ),
            pilot,
        )
        assert app.return_value is None

        cleanup_probe.blocker.set()
        await _wait_for(
            lambda: (
                "Something went wrong during browser sign-in. Please try again."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )

        await pilot.press("r")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert "sk-browser-onboarding-test-key" in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_switches_to_manual_path_without_cancelling_browser_sign_in_cleanup() -> (
    None
):
    cleanup_probe = BrowserSignInCleanupProbe()
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory(
            [RuntimeError("boom")], cleanup_probe=cleanup_probe
        )
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(cleanup_probe.started.is_set, pilot)

        await pilot.press("m")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)

        cleanup_probe.blocker.set()
        await _wait_for(cleanup_probe.finished.is_set, pilot)

    assert cleanup_probe.cancelled.is_set() is False


@pytest.mark.asyncio
async def test_ui_switches_to_manual_path_while_browser_sign_in_is_running() -> None:
    blocker = asyncio.Event()

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        poll_scripts=[build_completed_poll_script()], sleep=_blocked_sleep(blocker)
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )
    api_key_value = "sk-manual-after-browser-cancel"

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Waiting for authentication..."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        assert isinstance(app.screen, BrowserSignInScreen)
        assert app.screen.state.variant == "pending"
        step_cards = _browser_sign_in_step_cards(app.screen)
        assert len(step_cards) == 3
        assert step_cards[0].has_class("done")
        assert "Open browser" in _browser_sign_in_step_text(step_cards[0])
        assert "Sign-in page ready" in _browser_sign_in_step_text(step_cards[0])
        assert step_cards[1].has_class("active")
        assert "Complete sign-in" in _browser_sign_in_step_text(step_cards[1])
        assert "Waiting for authentication..." in _browser_sign_in_step_text(
            step_cards[1]
        )
        assert step_cards[2].has_class("idle")
        assert "Finished setup" in _browser_sign_in_step_text(step_cards[2])
        await pilot.press("m")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        await pilot.press(*api_key_value)
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert gateway.closed is True
    assert gateway.exchange_requests == []
    env_contents = _saved_env_contents()
    assert api_key_value in env_contents
    assert "sk-browser-onboarding-test-key" not in env_contents
