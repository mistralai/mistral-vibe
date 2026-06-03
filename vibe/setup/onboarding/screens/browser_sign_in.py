from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.worker import Worker

from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ProviderConfig
from vibe.core.logger import logger
from vibe.core.telemetry.types import EntrypointMetadata
from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInService,
    BrowserSignInStatus,
)
from vibe.setup.auth.api_key_persistence import (
    persist_api_key,
    resolve_api_key_provider,
)
from vibe.setup.onboarding.base import OnboardingScreen
from vibe.setup.onboarding.gradient_text import GRADIENT_COLORS, append_gradient_text

PENDING_HINT = "Press M to enter API key manually - Esc to cancel"
ERROR_HINT = "Press R to retry - Press M to enter API key manually - Esc to cancel"
SUCCESS_HINT = "Finishing setup..."
SUCCESS_EXIT_DELAY_SECONDS: float = 2.0
WAITING_FOR_AUTHENTICATION_MESSAGE = "Waiting for authentication..."
STEP_DESCRIPTIONS = [
    ("Open browser", "Your browser should open automatically", "Browser opened"),
    ("Complete sign-in", WAITING_FOR_AUTHENTICATION_MESSAGE, "Sign-in confirmed."),
    ("Finished setup", "Vibe will start automatically", "Setup complete."),
]
UNEXPECTED_ERROR_MESSAGE = (
    "Something went wrong during browser sign-in. Please try again."
)

ERROR_MESSAGES = {
    BrowserSignInErrorCode.POLL_FAILED: "We couldn't complete sign-in. Please try again."
}


class BrowserSignInStep(IntEnum):
    OPEN = 0
    CONFIRM = 1
    FINISH = 2


@dataclass(frozen=True)
class BrowserSignInViewState:
    step: BrowserSignInStep
    message: str
    hint: str
    variant: Literal["pending", "error", "success"]
    running: bool


@dataclass(frozen=True)
class BrowserSignInStepWidgets:
    marker: NoMarkupStatic
    card: Vertical
    title: NoMarkupStatic
    detail: NoMarkupStatic


class BrowserSignInScreen(OnboardingScreen):
    state = reactive(
        BrowserSignInViewState(
            step=BrowserSignInStep.OPEN,
            message="Getting things ready...",
            hint=PENDING_HINT,
            variant="pending",
            running=False,
        ),
        init=False,
    )

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("r", "retry", "Retry", show=False),
        Binding("m", "manual", "Manual", show=False),
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        provider: ProviderConfig,
        browser_sign_in_factory: Callable[[], BrowserSignInService],
        *,
        entrypoint_metadata: EntrypointMetadata | None = None,
        success_exit_delay: float = SUCCESS_EXIT_DELAY_SECONDS,
    ) -> None:
        super().__init__()
        self.provider = provider
        self._browser_sign_in_factory = browser_sign_in_factory
        self._entrypoint_metadata = entrypoint_metadata
        self._success_exit_delay = success_exit_delay
        self._attempt_number = 0
        self._active_attempt_number: int | None = None
        self._worker: Worker[None] | None = None
        self._gradient_offset = 0
        self._gradient_timer: Timer | None = None
        self._initial_state = BrowserSignInViewState(
            step=BrowserSignInStep.OPEN,
            message="Getting things ready...",
            hint=PENDING_HINT,
            variant="pending",
            running=False,
        )
        self._step_widgets: list[BrowserSignInStepWidgets] = []
        self._title_widget: NoMarkupStatic
        self._hint_widget: NoMarkupStatic

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-sign-in-content", classes="onboarding-content"):
            with Center():
                with Vertical(id="browser-sign-in-panel", classes="onboarding-panel"):
                    yield PetitChat(
                        id="browser-sign-in-chat", classes="onboarding-chat"
                    )
                    self._title_widget = NoMarkupStatic(
                        "Launch browser",
                        id="browser-sign-in-title",
                        classes="onboarding-heading",
                    )
                    yield self._title_widget
                    yield NoMarkupStatic(
                        "Your browser should open automatically",
                        id="browser-sign-in-subtitle",
                    )
                    with Vertical(id="browser-sign-in-steps"):
                        yield from self._compose_step_rows()
                    yield NoMarkupStatic("", id="browser-sign-in-hint")

    def _compose_step_rows(self) -> ComposeResult:
        self._step_widgets = []
        for _ in STEP_DESCRIPTIONS:
            with Horizontal(classes="browser-sign-in-step-row onboarding-option-row"):
                marker = NoMarkupStatic("", classes="browser-sign-in-step-marker")
                yield marker
                with Vertical(classes="browser-sign-in-step onboarding-card") as card:
                    title = NoMarkupStatic("", classes="browser-sign-in-step-title")
                    detail = NoMarkupStatic("", classes="browser-sign-in-step-detail")
                    self._step_widgets.append(
                        BrowserSignInStepWidgets(marker, card, title, detail)
                    )
                    yield title
                    yield detail

    def on_mount(self) -> None:
        self._hint_widget = self.query_one("#browser-sign-in-hint", NoMarkupStatic)
        self.state = self._initial_state
        self.watch_state(self.state)
        self._gradient_timer = self.set_interval(0.08, self._animate_gradient)
        self.call_after_refresh(self._start_browser_sign_in)

    def on_unmount(self) -> None:
        if self._gradient_timer is not None:
            self._gradient_timer.stop()
            self._gradient_timer = None
        self._cancel_current_attempt()

    def action_retry(self) -> None:
        if not self.state.running:
            self._start_browser_sign_in()

    def action_manual(self) -> None:
        if self.state.variant == "success":
            return
        self._cancel_current_attempt()
        self.app.switch_screen("api_key")

    def action_cancel(self) -> None:
        if self.state.variant == "success":
            return
        self._cancel_current_attempt()
        super().action_cancel()

    def _start_browser_sign_in(self) -> None:
        self._attempt_number += 1
        attempt_number = self._attempt_number
        self._active_attempt_number = attempt_number
        self.state = BrowserSignInViewState(
            step=BrowserSignInStep.OPEN,
            message="Getting things ready...",
            hint=PENDING_HINT,
            variant="pending",
            running=True,
        )
        self._worker = self.run_worker(
            self._authenticate_in_browser(attempt_number),
            group="browser-sign-in",
            exclusive=True,
        )

    async def _authenticate_in_browser(self, attempt_number: int) -> None:
        browser_sign_in: BrowserSignInService | None = None
        api_key: str | None = None
        error_message: str | None = None
        try:
            browser_sign_in = self._browser_sign_in_factory()
            api_key = await browser_sign_in.authenticate(
                lambda status: self._on_status(attempt_number, status)
            )
        except asyncio.CancelledError:
            return
        except BrowserSignInError as err:
            if not self._is_attempt_active(attempt_number):
                return
            logger.warning(
                "Browser sign-in flow failed for provider=%s attempt_number=%s code=%s message=%s",
                self.provider.name,
                attempt_number,
                err.code,
                err,
            )
            message = str(err)
            if err.code is not None:
                message = ERROR_MESSAGES.get(err.code, message)
            error_message = message
        except Exception:
            if not self._is_attempt_active(attempt_number):
                return
            logger.exception(
                "Unexpected browser sign-in flow failed for provider=%s attempt_number=%s",
                self.provider.name,
                attempt_number,
            )
            error_message = UNEXPECTED_ERROR_MESSAGE
        finally:
            await self._close_browser_sign_in(browser_sign_in)

        if not self._is_attempt_active(attempt_number):
            return
        if error_message is not None:
            self._show_error(error_message)
            return

        if api_key is None:
            msg = "Browser sign-in finished without returning an API key."
            raise AssertionError(msg)
        result = persist_api_key(
            resolve_api_key_provider(self.provider),
            api_key,
            entrypoint_metadata=self._entrypoint_metadata,
        )
        if result != "completed":
            self._active_attempt_number = None
            self._worker = None
            self.app.exit(result)
            return

        self.state = BrowserSignInViewState(
            step=BrowserSignInStep.FINISH,
            message="Sign-in complete",
            hint=SUCCESS_HINT,
            variant="success",
            running=True,
        )
        if self._success_exit_delay > 0:
            await asyncio.sleep(self._success_exit_delay)
        self._active_attempt_number = None
        self._worker = None
        self.app.exit(result)

    def _on_status(self, attempt_number: int, status: BrowserSignInStatus) -> None:
        if not self._is_attempt_active(attempt_number):
            return

        match status:
            case BrowserSignInStatus.OPENING_BROWSER:
                state = BrowserSignInViewState(
                    step=BrowserSignInStep.OPEN,
                    message="Opening your browser...",
                    hint=PENDING_HINT,
                    variant="pending",
                    running=True,
                )
            case BrowserSignInStatus.WAITING_FOR_BROWSER_SIGN_IN:
                state = BrowserSignInViewState(
                    step=BrowserSignInStep.CONFIRM,
                    message="Waiting for you to finish signing in...",
                    hint=PENDING_HINT,
                    variant="pending",
                    running=True,
                )
            case BrowserSignInStatus.EXCHANGING | BrowserSignInStatus.COMPLETED:
                state = BrowserSignInViewState(
                    step=BrowserSignInStep.FINISH,
                    message="Finishing setup...",
                    hint=PENDING_HINT,
                    variant="pending",
                    running=True,
                )
            case _:
                return

        self.state = state

    def watch_state(self, state: BrowserSignInViewState) -> None:
        if not self.is_mounted:
            return

        self._hint_widget.update(state.hint)

        for index, (widgets, (title, pending_detail, done_detail)) in enumerate(
            zip(self._step_widgets, STEP_DESCRIPTIONS, strict=True)
        ):
            if index < state.step:
                detail = done_detail
                widget_class = "done"
            elif index == state.step:
                detail = pending_detail
                widget_class = "active"
            else:
                detail = pending_detail
                widget_class = "idle"

            widgets.title.update(title)
            widgets.title.remove_class("done", "active", "idle")
            widgets.title.add_class(widget_class)
            widgets.detail.remove_class("done", "active", "idle")
            widgets.detail.remove_class("pending", "error", "success")
            if widget_class == "active":
                self._update_active_step_detail(widgets.detail, state)
            else:
                widgets.detail.update(detail)
                widgets.detail.add_class(widget_class)
            widgets.marker.update(">" if widget_class == "active" else "")
            widgets.marker.remove_class("done", "active", "idle")
            widgets.marker.add_class(widget_class)
            widgets.card.remove_class("done", "active", "idle")
            widgets.card.add_class(widget_class)

    def _update_active_step_detail(
        self, detail: NoMarkupStatic, state: BrowserSignInViewState
    ) -> None:
        if state.variant == "pending" and state.step == BrowserSignInStep.CONFIRM:
            content = Text()
            append_gradient_text(
                content, WAITING_FOR_AUTHENTICATION_MESSAGE, self._gradient_offset
            )
            detail.update(content)
            detail.add_class("pending")
            return

        if state.variant == "error":
            detail.update(state.message)
            detail.add_class("error")
            return

        detail.update(state.message)
        detail.add_class(state.variant)

    def _animate_gradient(self) -> None:
        self._gradient_offset = (self._gradient_offset + 1) % len(GRADIENT_COLORS)
        if (
            self.state.variant == "pending"
            and self.state.step == BrowserSignInStep.CONFIRM
        ):
            self.watch_state(self.state)

    async def _close_browser_sign_in(
        self, browser_sign_in: BrowserSignInService | None
    ) -> None:
        if browser_sign_in is None:
            return

        close_task = asyncio.create_task(browser_sign_in.aclose())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await asyncio.shield(close_task)
            raise

    def _show_error(self, message: str) -> None:
        self._active_attempt_number = None
        self._worker = None
        self.state = BrowserSignInViewState(
            step=self.state.step,
            message=message,
            hint=ERROR_HINT,
            variant="error",
            running=False,
        )

    def _cancel_current_attempt(self) -> None:
        self._active_attempt_number = None
        self.state = BrowserSignInViewState(
            step=self.state.step,
            message=self.state.message,
            hint=self.state.hint,
            variant=self.state.variant,
            running=False,
        )
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    def _is_attempt_active(self, attempt_number: int) -> bool:
        return self._active_attempt_number == attempt_number and self.state.running
