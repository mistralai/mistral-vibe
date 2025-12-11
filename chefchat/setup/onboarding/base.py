from __future__ import annotations

from textual.screen import Screen


class OnboardingScreen(Screen[str | None]):
    NEXT_SCREEN: str | None = None

    def action_next(self) -> None:
        if self.NEXT_SCREEN:
            self.app.switch_screen(self.NEXT_SCREEN)

    def action_cancel(self) -> None:
        self.app.exit(None)
