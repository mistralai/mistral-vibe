from __future__ import annotations

from collections.abc import Callable
import sys
from typing import Any

from rich import print as rprint
from textual.app import App

from vibe.cli.clipboard import try_copy_text_to_clipboard
from vibe.core.config import VibeConfig
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.telemetry.types import LaunchContext
from vibe.setup.auth import BrowserSignInService, HttpBrowserSignInGateway
from vibe.setup.onboarding.context import OnboardingContext
from vibe.setup.onboarding.screens import (
    ApiKeyScreen,
    AuthMethodScreen,
    BrowserSignInScreen,
    ThemeSelectionScreen,
    WelcomeScreen,
)
from vibe.setup.onboarding.screens.browser_sign_in import (
    SIGN_IN_URL_HELP_DELAY_SECONDS,
    SUCCESS_EXIT_DELAY_SECONDS,
    CopySignInUrl,
)


class OnboardingApp(App[str | None]):
    CSS_PATH = "onboarding.tcss"

    def __init__(
        self,
        config: OnboardingContext | VibeConfig | None = None,
        browser_sign_in_service_factory: Callable[[], BrowserSignInService]
        | None = None,
        launch_context: LaunchContext | None = None,
        browser_sign_in_success_delay: float = SUCCESS_EXIT_DELAY_SECONDS,
        browser_sign_in_url_help_delay: float = SIGN_IN_URL_HELP_DELAY_SECONDS,
        copy_sign_in_url: CopySignInUrl | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if config is None:
            config = OnboardingContext.load()
        elif isinstance(config, VibeConfig):
            config = OnboardingContext.from_config(config)

        self._config = config
        self._provider = config.provider
        self._vibe_base_url = config.vibe_base_url
        self._launch_context = launch_context
        self._browser_sign_in_success_delay = browser_sign_in_success_delay
        self._browser_sign_in_url_help_delay = browser_sign_in_url_help_delay
        self._copy_sign_in_url = copy_sign_in_url or self._copy_sign_in_url_to_clipboard
        self._browser_sign_in_service_factory = self._resolve_browser_sign_in_factory(
            browser_sign_in_service_factory
        )

    def on_mount(self) -> None:
        self.theme = "ansi-dark"

        theme_next = "auth_method" if self.supports_browser_sign_in else "api_key"
        welcome_screen = WelcomeScreen(next_screen="theme_selection")
        self.install_screen(welcome_screen, "welcome")
        self.install_screen(
            ThemeSelectionScreen(next_screen=theme_next), "theme_selection"
        )
        self.install_screen(
            ApiKeyScreen(
                self._provider,
                vibe_base_url=self._vibe_base_url,
                launch_context=self._launch_context,
            ),
            "api_key",
        )
        if self._browser_sign_in_service_factory is not None:
            self.install_screen(AuthMethodScreen(self._provider), "auth_method")
            self.install_screen(
                BrowserSignInScreen(
                    self._provider,
                    self._browser_sign_in_service_factory,
                    copy_sign_in_url=self._copy_sign_in_url,
                    launch_context=self._launch_context,
                    success_exit_delay=self._browser_sign_in_success_delay,
                    sign_in_url_help_delay=self._browser_sign_in_url_help_delay,
                ),
                "browser_sign_in",
            )
        self.push_screen("welcome")

    @property
    def supports_browser_sign_in(self) -> bool:
        return self._browser_sign_in_service_factory is not None

    def _build_browser_sign_in_service_factory(
        self,
    ) -> Callable[[], BrowserSignInService]:
        browser_base_url = self._provider.browser_auth_base_url
        api_base_url = self._provider.browser_auth_api_base_url
        if not browser_base_url or not api_base_url:
            msg = "Browser sign-in requires both browser auth URLs."
            raise AssertionError(msg)

        return lambda: BrowserSignInService(
            HttpBrowserSignInGateway(
                browser_base_url=browser_base_url, api_base_url=api_base_url
            )
        )

    def _resolve_browser_sign_in_factory(
        self, browser_sign_in_service_factory: Callable[[], BrowserSignInService] | None
    ) -> Callable[[], BrowserSignInService] | None:
        if not self._config.supports_browser_sign_in:
            return None

        return (
            browser_sign_in_service_factory
            or self._build_browser_sign_in_service_factory()
        )

    def _copy_sign_in_url_to_clipboard(self, text: str) -> bool:
        return try_copy_text_to_clipboard(text)


def run_onboarding(
    app: App | None = None, *, launch_context: LaunchContext | None = None
) -> None:
    result = (app or OnboardingApp(launch_context=launch_context)).run()
    match result:
        case None:
            rprint("\n[yellow]Setup cancelled. See you next time![/]")
            sys.exit(0)
        case str() as s if s.startswith("env_var_error:"):
            env_key = s.removeprefix("env_var_error:")
            rprint(
                "\n[yellow]Could not save the API key because this provider is "
                f"configured with an invalid environment variable name: {env_key}.[/]"
                "\n[dim]The API key was not saved for this session. "
                "Update the provider's `api_key_env_var` setting in your config and try again.[/]\n"
            )
            sys.exit(1)
        case str() as s if s.startswith("save_error:"):
            err = s.removeprefix("save_error:")
            rprint(
                f"\n[yellow]Warning: Could not save API key to .env file: {err}[/]"
                "\n[dim]The API key is set for this session only. "
                f"You may need to set it manually in {GLOBAL_ENV_FILE.path}[/]\n"
            )
        case "completed":
            rprint(
                '\nSetup complete 🎉. Run "vibe" to start using the Mistral Vibe CLI.\n'
            )
