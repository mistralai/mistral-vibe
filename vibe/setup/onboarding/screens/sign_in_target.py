from __future__ import annotations

from typing import ClassVar

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.setup.onboarding.base import OnboardingScreen

OPTION_MISTRAL = 0
OPTION_OTHER = 1


class SignInTargetScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selected_index = OPTION_MISTRAL
        self._override_confirm_armed = False
        self._option_markers: list[NoMarkupStatic] = []
        self._option_widgets: list[NoMarkupStatic] = []
        self._help_widget: NoMarkupStatic
        self._warning_widget: NoMarkupStatic

    def compose(self) -> ComposeResult:
        with Vertical(id="sign-in-target-content", classes="onboarding-content"):
            with Center():
                with Vertical(id="sign-in-target-panel", classes="onboarding-panel"):
                    yield PetitChat(id="sign-in-target-chat", classes="onboarding-chat")
                    yield NoMarkupStatic(
                        "Launch browser",
                        id="sign-in-target-title",
                        classes="onboarding-heading",
                    )
                    yield NoMarkupStatic(
                        "Where do you sign in?", id="sign-in-target-subtitle"
                    )
                    with Vertical(id="sign-in-target-options"):
                        yield from self._compose_option_rows()
                    self._warning_widget = NoMarkupStatic(
                        "", id="sign-in-target-warning"
                    )
                    yield self._warning_widget
                    self._help_widget = NoMarkupStatic(
                        "", id="sign-in-target-help", classes="onboarding-hint-row"
                    )
                    yield self._help_widget

    def _compose_option_rows(self) -> ComposeResult:
        self._option_markers = []
        self._option_widgets = []
        for index in range(2):
            if index == OPTION_OTHER:
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

    def on_screen_resume(self) -> None:
        self._disarm_override_confirm()

    def action_select(self) -> None:
        if self._selected_index == OPTION_OTHER:
            self._disarm_override_confirm()
            self.app.switch_screen("custom_domain")
            return
        configured_domain = self.onboarding_app.configured_custom_domain
        if configured_domain is not None and not self._override_confirm_armed:
            self._arm_override_confirm(configured_domain)
            return
        self.onboarding_app.apply_mistral_default_domain()
        self.app.switch_screen("browser_sign_in")

    def action_back(self) -> None:
        self.app.switch_screen("auth_method")

    def action_move_up(self) -> None:
        self._disarm_override_confirm()
        self._selected_index = (self._selected_index - 1) % len(self._option_widgets)
        self._update_display()

    def action_move_down(self) -> None:
        self._disarm_override_confirm()
        self._selected_index = (self._selected_index + 1) % len(self._option_widgets)
        self._update_display()

    def _arm_override_confirm(self, configured_domain: str) -> None:
        self._override_confirm_armed = True
        self._warning_widget.update(
            shortcut_hint(
                "This replaces your configured custom domain "
                f"({escape(configured_domain)}). "
                f"Press {shortcut('Enter')} again to continue."
            )
        )

    def _disarm_override_confirm(self) -> None:
        self._override_confirm_armed = False
        self._warning_widget.update("")

    def _update_display(self) -> None:
        options = [
            ("Mistral AI", "Recommended", "Sign in to Mistral AI Studio."),
            (
                "Other",
                None,
                "Sign in to a Mistral-compatible deployment on your own domain.",
            ),
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
                f"{shortcut('Esc')} Back"
            )
        )
