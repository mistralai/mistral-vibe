from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import keyring
from keyring.errors import KeyringError
import pytest
from textual.content import Content
from textual.events import Resize
from textual.geometry import Size
from textual.pilot import Pilot
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Input, Link, Static

from tests.browser_sign_in.stubs import (
    build_browser_sign_in_service_factory,
    build_sign_in_process,
)
from tests.conftest import build_test_vibe_config
from vibe.cli.textual_ui.shortcut_hints import SHORTCUT_STYLE
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import (
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    ModelConfig,
    ProviderConfig,
    VibeConfigSchema,
)
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import TerminalEmulator
from vibe.core.types import Backend
from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInEvent,
    BrowserSignInService,
    BrowserSignInStatus,
    BrowserSignInStatusChanged,
)
from vibe.setup.auth.api_key_persistence import persist_api_key
import vibe.setup.onboarding as onboarding_module
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.context import OnboardingContext
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen
from vibe.setup.onboarding.screens.auth_method import AuthMethodScreen
from vibe.setup.onboarding.screens.browser_sign_in import (
    SIGN_IN_URL_HELP_PREFIX,
    BrowserSignInScreen,
)
from vibe.setup.onboarding.screens.theme_selection import THEMES, ThemeSelectionScreen

CONSOLE_URL = "https://console.mistral.ai"
BROWSER_AUTH_API_URL = "https://console.mistral.ai/api"
TEST_NOW = datetime(2026, 3, 16, tzinfo=UTC)


@pytest.fixture(autouse=True)
def disable_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the .env fallback so persist tests never touch the real OS keyring."""

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("keyring disabled in tests")

    monkeypatch.setattr(keyring, "set_password", _unavailable)
    monkeypatch.setattr(keyring, "delete_password", lambda service, username: None)


def _expected_browser_sign_in_url(process_id: str = "process-1") -> str:
    return build_sign_in_process(TEST_NOW, process_id=process_id).sign_in_url


async def _wait_for(
    condition: Callable[[], bool],
    pilot: Pilot,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    elapsed = 0.0
    while not condition():
        await pilot.pause(interval)
        if (elapsed := elapsed + interval) >= timeout:
            raise AssertionError("Timed out waiting for condition.")


def _build_onboarding_config(
    *,
    provider_name: str = "mistral",
    model_provider: str | None = None,
    backend: Backend = Backend.MISTRAL,
    api_key_env_var: str = "MISTRAL_API_KEY",
    browser_auth_base_url: str | None = None,
    browser_auth_api_base_url: str | None = None,
    vibe_base_url: str = "https://chat.mistral.ai",
) -> VibeConfigSchema:
    provider = ProviderConfig(
        name=provider_name,
        api_base="https://api.mistral.ai/v1",
        api_key_env_var=api_key_env_var,
        browser_auth_base_url=browser_auth_base_url,
        browser_auth_api_base_url=browser_auth_api_base_url,
        backend=backend,
    )
    model = ModelConfig(
        name="mistral-vibe-cli-latest",
        provider=model_provider or provider_name,
        alias="devstral-2",
    )
    return build_test_vibe_config(
        providers=[provider], models=[model], vibe_base_url=vibe_base_url
    )


def _build_browser_onboarding_app(
    *,
    browser_sign_in_service_factory: Callable[[], BrowserSignInService] | None = None,
    browser_sign_in_success_delay: float = 0,
    browser_sign_in_url_help_delay: float = 0,
    copy_sign_in_url: Callable[[str], bool] | None = None,
) -> OnboardingApp:
    return OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
        browser_sign_in_success_delay=browser_sign_in_success_delay,
        browser_sign_in_url_help_delay=browser_sign_in_url_help_delay,
        copy_sign_in_url=copy_sign_in_url,
    )


def _patch_failing_browser_sign_in_service(
    monkeypatch: pytest.MonkeyPatch, captured_base_urls: list[tuple[str, str]]
) -> None:
    class FakeGateway:
        def __init__(self, browser_base_url: str, api_base_url: str) -> None:
            captured_base_urls.append((browser_base_url, api_base_url))

    class FakeService:
        def __init__(self, gateway: FakeGateway) -> None:
            self._gateway = gateway

        async def authenticate(self, *args, **kwargs) -> str:
            raise BrowserSignInError(
                "Browser sign-in polling failed.",
                code=BrowserSignInErrorCode.POLL_FAILED,
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(onboarding_module, "HttpBrowserSignInGateway", FakeGateway)
    monkeypatch.setattr(onboarding_module, "BrowserSignInService", FakeService)


def _saved_env_contents() -> str:
    return GLOBAL_ENV_FILE.path.read_text(encoding="utf-8")


def _browser_sign_in_step_cards(screen: Screen) -> list[Widget]:
    return list(screen.query(".browser-sign-in-step"))


def _browser_sign_in_step_card(screen: Screen, index: int) -> Widget:
    return _browser_sign_in_step_cards(screen)[index]


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


def _build_unexpected_browser_sign_in_service_factory(
    outcomes: list[str],
    *,
    api_key: str = "sk-browser-onboarding-test-key",
    close_blocker: asyncio.Event | None = None,
    close_started: asyncio.Event | None = None,
    close_finished: asyncio.Event | None = None,
    close_cancelled: asyncio.Event | None = None,
) -> Callable[[], BrowserSignInService]:
    remaining_outcomes = list(outcomes)

    class UnexpectedBrowserSignInService:
        def __init__(self, outcome: str) -> None:
            self._outcome = outcome

        async def authenticate(
            self, event_callback: Callable[[BrowserSignInEvent], None] | None = None
        ) -> str:
            if self._outcome == "completed":
                if event_callback is not None:
                    event_callback(
                        BrowserSignInStatusChanged(status=BrowserSignInStatus.COMPLETED)
                    )
                return api_key
            if self._outcome == "runtime_error":
                raise RuntimeError("boom")
            msg = f"Unsupported browser sign-in outcome: {self._outcome}"
            raise AssertionError(msg)

        async def aclose(self) -> None:
            try:
                if close_started is not None:
                    close_started.set()
                if close_blocker is not None:
                    await close_blocker.wait()
            except asyncio.CancelledError:
                if close_cancelled is not None:
                    close_cancelled.set()
                raise
            finally:
                if close_finished is not None:
                    close_finished.set()

            return None

    def build_service() -> BrowserSignInService:
        if not remaining_outcomes:
            msg = (
                "Unexpected browser sign-in service factory requires scripted outcomes."
            )
            raise AssertionError(msg)
        return cast(
            BrowserSignInService,
            UnexpectedBrowserSignInService(remaining_outcomes.pop(0)),
        )

    return build_service


async def _pass_welcome_screen(pilot: Pilot) -> None:
    welcome_screen = pilot.app.get_screen("welcome")
    await _wait_for(
        lambda: not welcome_screen.query_one("#enter-hint").has_class("hidden"), pilot
    )
    await pilot.press("enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, ThemeSelectionScreen), pilot)


async def _pass_theme_selection_screen(pilot: Pilot) -> None:
    await pilot.press("enter")


async def _show_auth_method(pilot: Pilot) -> None:
    await _pass_welcome_screen(pilot)
    await _pass_theme_selection_screen(pilot)
    await _wait_for(lambda: isinstance(pilot.app.screen, AuthMethodScreen), pilot)


async def _show_browser_sign_in(pilot: Pilot) -> None:
    await _show_auth_method(pilot)
    await pilot.press("enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, BrowserSignInScreen), pilot)


async def _show_manual_api_key_screen(pilot: Pilot) -> None:
    await _show_auth_method(pilot)
    await pilot.press("down", "enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)


@pytest.mark.asyncio
async def test_ui_keeps_manual_flow_when_browser_sign_in_is_unsupported() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url="", browser_auth_api_base_url=""
        )
    )
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)
        await _pass_theme_selection_screen(pilot)
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        input_widget = app.screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"
    assert api_key_value in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_supports_browser_sign_in_when_provider_supports_it() -> None:
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"]
    )
    app = OnboardingApp(
        config=_build_onboarding_config(
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        ),
        browser_sign_in_service_factory=browser_sign_in_service_factory,
    )

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)


@pytest.mark.asyncio
async def test_ui_offers_browser_sign_in_for_renamed_mistral_provider() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(
            provider_name="customer-mistral",
            backend=Backend.MISTRAL,
            browser_auth_base_url=CONSOLE_URL,
            browser_auth_api_base_url=BROWSER_AUTH_API_URL,
        )
    )

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)


@pytest.mark.asyncio
async def test_ui_allows_manual_path_when_browser_sign_in_is_supported() -> None:
    app = _build_browser_onboarding_app()
    api_key_value = "sk-manual-onboarding-test-key"

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)
        await pilot.press("down", "enter")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)
        input_widget = app.screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)
        assert input_widget.value == api_key_value

    assert app.return_value == "completed"
    assert api_key_value in _saved_env_contents()


@pytest.mark.asyncio
async def test_ui_does_not_show_browser_opened_before_attempt_starts() -> None:
    authenticate_started = asyncio.Event()
    finish_authenticate = asyncio.Event()
    keep_authenticate_running = asyncio.Event()
    copied_urls: list[str] = []

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    class DelayedBrowserSignInService:
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
            BrowserSignInService, DelayedBrowserSignInService()
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
        assert "Browser opened" not in active_step_text
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

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
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

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
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

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
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

    assert copied_urls == [_expected_browser_sign_in_url()]


@pytest.mark.asyncio
async def test_ui_copies_browser_sign_in_url_when_help_text_is_clicked() -> None:
    blocker = asyncio.Event()
    copied_urls: list[str] = []

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
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

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
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


@pytest.mark.asyncio
async def test_ui_keeps_last_sign_in_url_copy_prompt_after_open_browser_failure() -> (
    None
):
    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], open_browser=lambda _: False
    )
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=browser_sign_in_service_factory
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(
            lambda: (
                "Failed to open browser for sign-in."
                in _browser_sign_in_step_text(
                    _active_browser_sign_in_step_card(app.screen)
                )
            ),
            pilot,
        )
        assert _browser_sign_in_hint(app.screen) == (
            "Press r to retry - Press m to enter API key manually - Esc to cancel"
        )
        _assert_browser_sign_in_shortcuts_styled(app.screen)
        url_text = _browser_sign_in_url_text(app.screen)
        assert "If your browser did not open, copy this URL (press c)" in url_text
        assert "process-1" not in url_text


@pytest.mark.asyncio
async def test_ui_copies_last_browser_sign_in_url_after_open_browser_failure() -> None:
    copied_urls: list[str] = []

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], open_browser=lambda _: False
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

    assert copied_urls == [_expected_browser_sign_in_url()]


@pytest.mark.asyncio
async def test_ui_retry_hides_old_sign_in_url_and_uses_fresh_attempt_url() -> None:
    blocker = asyncio.Event()
    copied_urls: list[str] = []

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    def copy_sign_in_url(url: str) -> bool:
        copied_urls.append(url)
        return True

    _, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["expired", "completed"], sleep=wait_forever
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
        build_browser_sign_in_service_factory(outcomes=["expired", "completed"])
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
        outcomes=["completed"]
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
        outcomes=["completed"]
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
        outcomes=["completed"]
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
        outcomes=["poll_failed"]
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
            "runtime_error"
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
            "runtime_error",
            "completed",
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
    close_started = asyncio.Event()
    close_blocker = asyncio.Event()
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory(
            ["runtime_error", "completed"],
            close_blocker=close_blocker,
            close_started=close_started,
        )
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(close_started.is_set, pilot)

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

        close_blocker.set()
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
    close_started = asyncio.Event()
    close_blocker = asyncio.Event()
    close_finished = asyncio.Event()
    close_cancelled = asyncio.Event()
    app = _build_browser_onboarding_app(
        browser_sign_in_service_factory=_build_unexpected_browser_sign_in_service_factory(
            ["runtime_error"],
            close_blocker=close_blocker,
            close_started=close_started,
            close_finished=close_finished,
            close_cancelled=close_cancelled,
        )
    )

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(close_started.is_set, pilot)

        await pilot.press("m")
        await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)

        close_blocker.set()
        await _wait_for(close_finished.is_set, pilot)

    assert close_cancelled.is_set() is False


@pytest.mark.asyncio
async def test_ui_switches_to_manual_path_while_browser_sign_in_is_running() -> None:
    blocker = asyncio.Event()

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    gateway, browser_sign_in_service_factory, _ = build_browser_sign_in_service_factory(
        outcomes=["completed"], sleep=wait_forever
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
        assert "Browser opened" in _browser_sign_in_step_text(step_cards[0])
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


@pytest.mark.asyncio
async def test_ui_uses_default_mistral_browser_auth_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_base_urls: list[tuple[str, str]] = []
    _patch_failing_browser_sign_in_service(monkeypatch, captured_base_urls)

    app = OnboardingApp(config=build_test_vibe_config())

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: bool(captured_base_urls), pilot)

    assert captured_base_urls == [
        (
            DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
            DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
        )
    ]


@pytest.mark.asyncio
async def test_ui_preserves_custom_browser_auth_urls_when_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join([
            'active_model = "devstral-2"',
            "[[providers]]",
            'name = "mistral"',
            'api_base = "https://api.mistral.ai/v1"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            'browser_auth_base_url = "http://127.0.0.1:8787"',
            'browser_auth_api_base_url = "http://127.0.0.1:8787"',
            'backend = "mistral"',
            "",
            "[[models]]",
            'name = "mistral-vibe-cli-latest"',
            'provider = "mistral"',
            'alias = "devstral-2"',
            "",
        ]),
        encoding="utf-8",
    )
    reset_harness_files_manager()
    init_harness_files_manager("user")
    captured_base_urls: list[tuple[str, str]] = []
    _patch_failing_browser_sign_in_service(monkeypatch, captured_base_urls)

    app = OnboardingApp()

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_browser_sign_in(pilot)
        await _wait_for(lambda: bool(captured_base_urls), pilot)

    assert captured_base_urls == [("http://127.0.0.1:8787", "http://127.0.0.1:8787")]


@pytest.mark.asyncio
async def test_ui_falls_back_to_default_onboarding_context_with_invalid_active_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join([
            'active_model = "does-not-exist"',
            "",
            "[[providers]]",
            'name = "mistral"',
            'api_base = "https://api.mistral.ai/v1"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            'browser_auth_base_url = "https://console.mistral.ai"',
            'browser_auth_api_base_url = "https://console.mistral.ai/api"',
            'backend = "mistral"',
            "",
            "[[models]]",
            'name = "mistral-vibe-cli-latest"',
            'provider = "mistral"',
            'alias = "devstral-2"',
            "",
        ]),
        encoding="utf-8",
    )
    reset_harness_files_manager()
    init_harness_files_manager("user")

    app = OnboardingApp()

    assert app.supports_browser_sign_in is True

    async with app.run_test() as pilot:
        await _show_auth_method(pilot)


@pytest.mark.asyncio
async def test_ui_can_pick_a_theme_and_saves_selection() -> None:
    app = OnboardingApp()

    async with app.run_test() as pilot:
        await _pass_welcome_screen(pilot)

        theme_screen = app.screen
        assert isinstance(theme_screen, ThemeSelectionScreen)
        app.post_message(Resize(Size(40, 10), Size(40, 10)))
        preview = theme_screen.query_one("#preview")
        assert preview.styles.max_height is not None

        target_theme = "gruvbox"
        assert target_theme in THEMES
        start_index = THEMES.index(app.theme)
        target_index = THEMES.index(target_theme)
        steps_down = (target_index - start_index) % len(THEMES)
        await pilot.press(*["down"] * steps_down)
        assert app.theme == target_theme

        await pilot.press("enter")
        await _wait_for(lambda: isinstance(app.screen, AuthMethodScreen), pilot)

    # Persistence is the caller's job; the app carries the selection out via its
    # live theme for the caller to persist through its orchestrator.
    assert app.theme == target_theme


def test_api_key_screen_falls_back_to_mistral_for_provider_without_env_key() -> None:
    screen = ApiKeyScreen(
        provider=ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        )
    )

    assert screen.provider.name == "mistral"
    assert screen.provider.api_key_env_var == "MISTRAL_API_KEY"


def test_api_key_screen_keeps_provider_with_explicit_env_key() -> None:
    provider = ProviderConfig(
        name="custom",
        api_base="https://custom.example/v1",
        api_key_env_var="CUSTOM_API_KEY",
    )

    screen = ApiKeyScreen(provider=provider)

    assert screen.provider == provider


def test_api_key_screen_uses_mistral_fallback_for_context_without_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.setup.auth.api_key_persistence._load_onboarding_provider",
        lambda: ProviderConfig(
            name="llamacpp", api_base="http://127.0.0.1:8080/v1", api_key_env_var=""
        ),
    )

    screen = ApiKeyScreen()

    assert screen.provider.name == "mistral"
    assert screen.provider.api_key_env_var == "MISTRAL_API_KEY"


@pytest.mark.asyncio
async def test_ui_manual_api_key_screen_uses_configured_vibe_url() -> None:
    app = OnboardingApp(
        config=_build_onboarding_config(vibe_base_url="https://vibe.example.com/")
    )

    async with app.run_test() as pilot:
        await _show_manual_api_key_screen(pilot)

        provider_link = app.screen.query_one("#api-key-provider-link", Link)

    assert provider_link.url == "https://vibe.example.com/code/extensions?focus=key"


def test_persist_api_key_returns_save_error_for_invalid_env_var_name() -> None:
    provider = ProviderConfig(
        name="custom", api_base="https://custom.example/v1", api_key_env_var="BAD=NAME"
    )

    result = persist_api_key(provider, "secret")

    assert result == "env_var_error:BAD=NAME"


def test_persist_api_key_returns_env_var_error_for_empty_env_var_name() -> None:
    provider = ProviderConfig(
        name="custom", api_base="https://custom.example/v1", api_key_env_var=""
    )

    result = persist_api_key(provider, "secret")

    assert result == "env_var_error:<empty>"


def test_persist_api_key_sends_onboarding_telemetry_with_launch_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_metadata: dict[str, str] = {}

    def capture(self: TelemetryClient) -> None:
        recorded_metadata.update(self.build_client_event_metadata())

    monkeypatch.setattr(TelemetryClient, "send_onboarding_api_key_added", capture)

    provider = ProviderConfig(
        name="mistral",
        api_base="https://inference.mistral.test/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )

    result = persist_api_key(
        provider,
        "secret",
        launch_context=build_launch_context(
            agent_entrypoint="cli",
            agent_version="1.0.0",
            client_name="vibe_cli",
            client_version="1.0.0",
            terminal_emulator=TerminalEmulator.APPLE_TERMINAL,
        ),
    )

    assert result == "completed"
    assert recorded_metadata["agent_entrypoint"] == "cli"
    assert recorded_metadata["agent_version"] == "1.0.0"
    assert recorded_metadata["client_name"] == "vibe_cli"
    assert recorded_metadata["client_version"] == "1.0.0"
    assert recorded_metadata["terminal_emulator"] == "apple_terminal"
    assert "session_id" not in recorded_metadata
