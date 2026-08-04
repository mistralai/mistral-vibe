from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.screen import Screen

if TYPE_CHECKING:
    from vibe.setup.onboarding import OnboardingApp


class OnboardingScreen(Screen[str | None]):
    NEXT_SCREEN: str | None = None

    @property
    def onboarding_app(self) -> OnboardingApp:
        return cast("OnboardingApp", self.app)

    def action_next(self) -> None:
        if self.NEXT_SCREEN:
            self.app.switch_screen(self.NEXT_SCREEN)

    def action_cancel(self) -> None:
        self.app.exit(None)
