from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, override

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Vertical
from textual.validation import Function, ValidationResult
from textual.widgets import Input

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.setup.onboarding.base import OnboardingScreen
from vibe.setup.onboarding.context import (
    is_likely_mistral_private_cloud_domain,
    is_valid_custom_domain,
)

if TYPE_CHECKING:
    from textual.widget import Widget


def _is_valid_optional_custom_domain(value: str) -> bool:
    stripped = value.strip()
    return not stripped or is_valid_custom_domain(stripped)


class CustomDomainScreen(OnboardingScreen):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
    ]

    NEXT_SCREEN = None

    _suppress_domain_feedback: bool = False
    _suppress_api_base_feedback: bool = False
    domain_input: Input
    api_base_input: Input

    def __init__(self) -> None:
        super().__init__()
        self.domain_input = Input(
            placeholder="console.mistral.ai",
            id="domain",
            select_on_focus=False,
            validators=[Function(is_valid_custom_domain, "Enter a valid domain URL.")],
        )
        self.api_base_input = Input(
            placeholder="https://connector.internal.example/api",
            id="api-base",
            select_on_focus=False,
            validators=[
                Function(
                    _is_valid_optional_custom_domain, "Enter a valid API base URL."
                )
            ],
        )

    @override
    def compose(self) -> ComposeResult:
        domain_box = Vertical(
            self.domain_input, id="domain-box", classes="onboarding-card"
        )
        domain_box.border_title = "Custom domain"

        api_base_box = Vertical(
            self.api_base_input, id="api-base-box", classes="onboarding-card"
        )
        # The browser-auth /api sign-in host, NOT the /v1 LLM API base. Only
        # needed for split-horizon deployments where the CLI reaches the console
        # under a different host than the browser.
        api_base_box.border_title = "Browser-auth API base (optional)"

        with Vertical(id="custom-domain-outer", classes="onboarding-content"):
            with Center():
                with Vertical(id="custom-domain-panel", classes="onboarding-panel"):
                    yield PetitChat(id="custom-domain-chat", classes="onboarding-chat")
                    yield NoMarkupStatic(
                        "Use a custom domain",
                        id="custom-domain-title",
                        classes="onboarding-heading",
                    )
                    yield NoMarkupStatic(
                        "Point Vibe at a Mistral-compatible deployment.",
                        id="custom-domain-subtitle",
                    )
                    yield domain_box
                    yield api_base_box
                    yield NoMarkupStatic("", id="feedback")
                    yield NoMarkupStatic(
                        shortcut_hint(f"Press {shortcut('Esc')} to go back"),
                        id="custom-domain-help",
                        classes="onboarding-hint-row",
                    )

    def on_mount(self) -> None:
        self._reset_input()

    def on_screen_resume(self) -> None:
        self._reset_input()

    def _reset_input(self) -> None:
        domain_seed = self.onboarding_app.configured_custom_domain or ""
        api_seed = self.onboarding_app.configured_custom_api_base or ""
        self._suppress_domain_feedback = bool(domain_seed) or bool(
            self.domain_input.value
        )
        self._suppress_api_base_feedback = bool(api_seed) or bool(
            self.api_base_input.value
        )
        self.domain_input.value = domain_seed
        self.domain_input.cursor_position = len(domain_seed)
        self.api_base_input.value = api_seed
        self.api_base_input.cursor_position = len(api_seed)
        self.domain_input.focus()
        feedback = self.query_one("#feedback", NoMarkupStatic)
        self._reset_feedback(feedback, self.query_one("#domain-box"))
        self._reset_feedback(feedback, self.query_one("#api-base-box"))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "api-base":
            if self._suppress_api_base_feedback:
                self._suppress_api_base_feedback = False
                return
            self._render_api_base_feedback(event.value, event.validation_result)
            return
        if self._suppress_domain_feedback:
            self._suppress_domain_feedback = False
            return
        self._render_domain_feedback(event.value, event.validation_result)

    def _render_domain_feedback(
        self, value: str, result: ValidationResult | None
    ) -> None:
        if result is None:
            return
        feedback = self.query_one("#feedback", NoMarkupStatic)
        box = self.query_one("#domain-box")
        self._reset_feedback(feedback, box)
        if result.is_valid:
            if value.strip() and is_likely_mistral_private_cloud_domain(value):
                feedback.update(
                    shortcut_hint(
                        "Mistral private-cloud domain detected."
                        + " For Mistral-hosted accounts, use console.mistral.ai."
                        + " Proceed only for self-hosted deployments."
                    )
                )
                feedback.add_class("warning")
                box.add_class("warning")
                return
            feedback.update(shortcut_hint(f"Press {shortcut('Enter')} to submit ↵"))
            feedback.add_class("success")
            box.add_class("valid")
            return
        feedback.update(result.failure_descriptions[0])
        feedback.add_class("error")
        box.add_class("invalid")

    def _render_api_base_feedback(
        self, value: str, result: ValidationResult | None
    ) -> None:
        feedback = self.query_one("#feedback", NoMarkupStatic)
        box = self.query_one("#api-base-box")
        self._reset_feedback(feedback, box)
        if not value.strip():
            return
        if result is not None and not result.is_valid:
            feedback.update(result.failure_descriptions[0])
            feedback.add_class("error")
            box.add_class("invalid")
            return
        feedback.update(shortcut_hint(f"Press {shortcut('Enter')} to submit ↵"))
        feedback.add_class("success")
        box.add_class("valid")

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        domain = self.domain_input.value.strip()
        if not is_valid_custom_domain(domain):
            self._render_domain_feedback(domain, self.domain_input.validate(domain))
            return
        api_base = self.api_base_input.value.strip()
        if api_base and not is_valid_custom_domain(api_base):
            self._render_api_base_feedback(
                api_base, self.api_base_input.validate(api_base)
            )
            return
        app = self.onboarding_app
        app.apply_custom_domain(domain, api_base or None)
        app.switch_screen("browser_sign_in")

    def action_back(self) -> None:
        self.app.switch_screen("sign_in_target")

    def _reset_feedback(self, feedback: NoMarkupStatic, box: Widget) -> None:
        feedback.update("")
        _ = feedback.remove_class("error", "success", "warning")
        _ = box.remove_class("valid", "invalid", "warning")
