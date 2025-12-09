from __future__ import annotations

import sys

from rich import print as rprint
from textual.app import App

from vibe.core.config import GLOBAL_ENV_FILE
from vibe.setup.onboarding.screens import (
    ApiKeyScreen,
    ThemeSelectionScreen,
    WelcomeScreen,
)


class OnboardingApp(App[str | None]):
    CSS_PATH = "onboarding.tcss"

    def on_mount(self) -> None:
        self.install_screen(WelcomeScreen(), "welcome")
        self.install_screen(ThemeSelectionScreen(), "theme_selection")
        self.install_screen(ApiKeyScreen(), "api_key")
        self.push_screen("welcome")


def run_onboarding(app: App | None = None) -> None:
    result = (app or OnboardingApp()).run()
    match result:
        case None:
            rprint("\n[yellow]Setup cancelled. See you next time![/]")
            sys.exit(0)
        case str() as s if s.startswith("save_error:"):
            err = s.removeprefix("save_error:")
            rprint(
                f"\n[yellow]Warning: Could not save API key to .env file: {err}[/]"
                "\n[dim]The API key is set for this session only. "
                f"You may need to set it manually in {GLOBAL_ENV_FILE}[/]\n"
            )
        case "completed":
            pass
