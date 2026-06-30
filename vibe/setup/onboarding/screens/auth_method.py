from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ProviderConfig
from vibe.setup.onboarding.base import OnboardingScreen

OPTION_BROWSER = 0
OPTION_MANUAL = 1


class AuthMethodScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, provider: ProviderConfig) -> None:
        super().__init__()
        self.provider = provider
        self._selected_index = OPTION_BROWSER
        self._option_markers: list[NoMarkupStatic] = []
        self._option_widgets: list[NoMarkupStatic] = []
        self._help_widget: NoMarkupStatic

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-method-content", classes="onboarding-content"):
            with Center():
                with Vertical(id="auth-method-panel", classes="onboarding-panel"):
                    yield PetitChat(id="auth-method-chat", classes="onboarding-chat")
                    yield NoMarkupStatic(
                        "Welcome to Mistral Vibe",
                        id="auth-method-title",
                        classes="onboarding-heading",
                    )
                    yield NoMarkupStatic(
                        "Choose your sign in method", id="auth-method-subtitle"
                    )
                    with Vertical(id="auth-method-options"):
                        yield from self._compose_option_rows()
                    self._help_widget = NoMarkupStatic(
                        "", id="auth-method-help", classes="onboarding-hint-row"
                    )
                    yield self._help_widget

    def _compose_option_rows(self) -> ComposeResult:
        self._option_markers = []
        self._option_widgets = []
        for index in range(2):
            if index == OPTION_MANUAL:
                yield NoMarkupStatic("or", classes="auth-method-separator")
            with Horizontal(classes="auth-method-option-row onboarding-option-row"):
                marker = NoMarkupStatic("", classes="auth-method-option-marker")
                option = NoMarkupStatic(
                    "", classes="auth-method-option onboarding-card"
                )
                self._option_markers.append(marker)
                self._option_widgets.append(option)
                yield marker
                yield option

    def on_mount(self) -> None:
        self._update_display()
        self.focus()

    def action_select(self) -> None:
        if self._selected_index == OPTION_BROWSER:
            self.action_browser()
            return
        self.action_manual()

    def action_manual(self) -> None:
        self.app.switch_screen("api_key")

    def action_browser(self) -> None:
        self.app.switch_screen("browser_sign_in")

    def action_move_up(self) -> None:
        self._selected_index = (self._selected_index - 1) % len(self._option_widgets)
        self._update_display()

    def action_move_down(self) -> None:
        self._selected_index = (self._selected_index + 1) % len(self._option_widgets)
        self._update_display()

    def _update_display(self) -> None:
        options = [
            (
                "Launch browser",
                "Recommended",
                "Sign in to Mistral AI Studio and finish setup automatically.",
            ),
            ("Use an API key", None, "Already have a key? Paste it manually instead."),
        ]

        for index, (marker, widget, (title, badge, description)) in enumerate(
            zip(self._option_markers, self._option_widgets, options, strict=True)
        ):
            is_selected = index == self._selected_index
            content = Text()
            content.append(title, style="bold")
            content.append("\n")
            content.append(description, style="dim")
            marker.update(">" if is_selected else "")
            marker.remove_class("selected")
            widget.border_title = badge
            widget.update(content)
            widget.remove_class("selected")
            if is_selected:
                marker.add_class("selected")
                widget.add_class("selected")

        self._help_widget.update(
            shortcut_hint(
                f"Use {shortcut('↑↓')} to navigate - {shortcut('Enter')} Select - "
                f"{shortcut('Esc')} Cancel"
            )
        )
