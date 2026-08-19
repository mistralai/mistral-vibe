from __future__ import annotations

import asyncio
from collections.abc import Callable
import sys
from typing import Any

from rich import print as rprint
from textual.app import App

from vibe.cli.clipboard import copy_to_clipboard
from vibe.cli.theme import resolve_auto_theme, resolve_theme, resolve_theme_name
from vibe.core.config import VibeConfigSchema
from vibe.core.config._defaults import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    DEFAULT_MISTRAL_SERVER_URL,
    DEFAULT_VIBE_BASE_URL,
)
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.telemetry.types import LaunchContext
from vibe.setup.auth import BrowserSignInService, HttpBrowserSignInGateway
from vibe.setup.auth.api_key_persistence import (
    ProviderCredentialsPersistRequest,
    persist_api_key,
    persist_provider_credentials,
    resolve_api_key_provider,
)
from vibe.setup.auth.whoami import resolve_tenant_domains
from vibe.setup.onboarding.context import OnboardingContext, resolve_browser_auth_urls
from vibe.setup.onboarding.screens import (
    ApiKeyScreen,
    AuthMethodScreen,
    BrowserSignInScreen,
    CustomDomainScreen,
    SignInTargetScreen,
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
        config: OnboardingContext | VibeConfigSchema | None = None,
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
        elif isinstance(config, VibeConfigSchema):
            config = OnboardingContext.from_config(config)

        self._config = config
        self._initial_theme = resolve_theme_name(config.theme)
        resolve_auto_theme()
        self._resolved_theme = resolve_theme(self._initial_theme)
        self._theme_selection_screen: ThemeSelectionScreen | None = None
        self._provider = config.provider
        self._vibe_base_url = config.vibe_base_url
        self._console_base_url = config.console_base_url
        self._launch_context = launch_context
        self._browser_sign_in_success_delay = browser_sign_in_success_delay
        self._browser_sign_in_url_help_delay = browser_sign_in_url_help_delay
        self._copy_sign_in_url = copy_sign_in_url or self._copy_sign_in_url_to_clipboard
        self._browser_sign_in_service_factory = self._resolve_browser_sign_in_factory(
            browser_sign_in_service_factory
        )

    def on_mount(self) -> None:
        self.theme = self._resolved_theme

        theme_next = "auth_method" if self.supports_browser_sign_in else "api_key"
        welcome_screen = WelcomeScreen(next_screen="theme_selection")
        self.install_screen(welcome_screen, "welcome")
        self._theme_selection_screen = ThemeSelectionScreen(
            initial_theme=self._initial_theme, next_screen=theme_next
        )
        self.install_screen(self._theme_selection_screen, "theme_selection")
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
            self.install_screen(SignInTargetScreen(), "sign_in_target")
            self.install_screen(CustomDomainScreen(), "custom_domain")
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

    @property
    def selected_theme(self) -> str:
        if self._theme_selection_screen is None:
            return self._initial_theme
        return self._theme_selection_screen.selected_theme

    @property
    def configured_custom_domain(self) -> str | None:
        base_url = self._config.provider.browser_auth_base_url
        if not base_url or base_url == DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL:
            return None
        return base_url

    def apply_custom_domain(self, domain: str) -> None:
        browser_base_url, browser_api_base_url = resolve_browser_auth_urls(domain)
        self._provider = self._provider.model_copy(
            update={
                "browser_auth_base_url": browser_base_url,
                "browser_auth_api_base_url": browser_api_base_url,
            }
        )
        # Keep the top-level console URL (used for /whoami, plan lookups) in sync
        # with the provider's browser auth host — otherwise on-prem users sign in
        # against their tenant but every account call still hits console.mistral.ai.
        self._console_base_url = browser_base_url

    def apply_mistral_default_domain(self) -> None:
        self._provider = self._provider.model_copy(
            update={
                "browser_auth_base_url": DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
                "browser_auth_api_base_url": DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
                "api_base": f"{DEFAULT_MISTRAL_SERVER_URL}/v1",
            }
        )
        self._console_base_url = DEFAULT_CONSOLE_BASE_URL
        self._vibe_base_url = DEFAULT_VIBE_BASE_URL

    async def persist_credentials(self, api_key: str) -> str:
        """Persist the API key and, for on-prem sign-ins, resolve tenant domains
        from /whoami before writing provider/console/vibe URLs to config.
        """
        resolved = resolve_api_key_provider(self._provider)
        base_url = self._provider.browser_auth_base_url
        result = persist_api_key(
            resolved,
            api_key,
            launch_context=self._launch_context,
            custom_domain=bool(
                base_url and base_url != DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL
            ),
        )
        if result != "completed":
            return result
        if (
            self._provider == self._config.provider
            and self._console_base_url == self._config.console_base_url
            and self._vibe_base_url == self._config.vibe_base_url
        ):
            return result

        # Only fetch tenant domains for on-prem consoles — the public Mistral
        # console has no per-tenant redirection to discover.
        if self._console_base_url != DEFAULT_CONSOLE_BASE_URL:
            self._provider, self._vibe_base_url = await resolve_tenant_domains(
                self._provider, self._console_base_url, api_key, self._vibe_base_url
            )

        request = ProviderCredentialsPersistRequest(
            provider=self._provider,
            console_base_url=(
                self._console_base_url
                if self._console_base_url != self._config.console_base_url
                else None
            ),
            vibe_base_url=(
                self._vibe_base_url
                if self._vibe_base_url != self._config.vibe_base_url
                else None
            ),
        )
        outcome = await persist_provider_credentials(request)
        failure = outcome.first_failure()
        if failure is not None:
            return f"provider_config_error:failed to persist {failure}"
        return result

    def _build_browser_sign_in_service_factory(
        self,
    ) -> Callable[[], BrowserSignInService]:
        def factory() -> BrowserSignInService:
            browser_base_url = self._provider.browser_auth_base_url
            api_base_url = self._provider.browser_auth_api_base_url
            if not browser_base_url or not api_base_url:
                msg = "Browser sign-in requires both browser auth URLs."
                raise AssertionError(msg)
            return BrowserSignInService(
                HttpBrowserSignInGateway(
                    browser_base_url=browser_base_url, api_base_url=api_base_url
                )
            )

        return factory

    def _resolve_browser_sign_in_factory(
        self, browser_sign_in_service_factory: Callable[[], BrowserSignInService] | None
    ) -> Callable[[], BrowserSignInService] | None:
        if not self._config.supports_browser_sign_in:
            return None

        return (
            browser_sign_in_service_factory
            or self._build_browser_sign_in_service_factory()
        )

    def _copy_sign_in_url_to_clipboard(self, text: str) -> None:
        copy_to_clipboard(text)


def run_onboarding(
    app: App | None = None, *, launch_context: LaunchContext | None = None
) -> ConfigOrchestrator[VibeConfigSchema]:
    onboarding_app = app or OnboardingApp(launch_context=launch_context)
    result = onboarding_app.run()
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
        case str() as s if s.startswith("provider_config_error:"):
            err = s.removeprefix("provider_config_error:")
            rprint(
                f"\n[yellow]Warning: Could not save provider config: {err}[/]"
                "\n[dim]The API key was saved, but the custom domain provider "
                "configuration was not persisted to config.toml. "
                "You may need to update your provider's "
                "`browser_auth_base_url` / `browser_auth_api_base_url` "
                "settings manually.[/]\n"
            )
        case "completed":
            rprint(
                '\nSetup complete 🎉. Run "vibe" to start using the Mistral Vibe CLI.\n'
            )
    theme = (
        onboarding_app.selected_theme
        if isinstance(onboarding_app, OnboardingApp)
        else onboarding_app.theme
    )
    orchestrator = asyncio.run(build_default_orchestrator())
    if theme is not None:
        asyncio.run(orchestrator.set_field("/theme", theme, reason="onboarding"))
    return orchestrator
