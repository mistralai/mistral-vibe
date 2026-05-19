from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Vertical
from textual.reactive import reactive
from textual.worker import Worker

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
from vibe.setup.onboarding.base import OnboardingScreen
from vibe.setup.onboarding.screens.api_key import (
    _resolve_onboarding_provider,
    persist_api_key,
)

PENDING_HINT = "Press M to enter API key manually · Esc to cancel"
ERROR_HINT = "Press R to retry · Press M to enter API key manually · Esc to cancel"
STEP_DESCRIPTIONS = ["Open your browser", "Sign in and return here", "Finish setup"]
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
    ) -> None:
        super().__init__()
        self.provider = provider
        self._browser_sign_in_factory = browser_sign_in_factory
        self._entrypoint_metadata = entrypoint_metadata
        self._attempt_number = 0
        self._active_attempt_number: int | None = None
        self._worker: Worker[None] | None = None
        self._initial_state = BrowserSignInViewState(
            step=BrowserSignInStep.OPEN,
            message="Getting things ready...",
            hint=PENDING_HINT,
            variant="pending",
            running=False,
        )
        self._step_widgets: list[NoMarkupStatic] = []
        self._title_widget: NoMarkupStatic
        self._subtitle_widget: NoMarkupStatic
        self._status_widget: NoMarkupStatic
        self._hint_widget: NoMarkupStatic

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-sign-in-content"):
            with Center():
                with Vertical(id="browser-sign-in-card"):
                    self._title_widget = NoMarkupStatic(
                        "Sign in with your browser", id="browser-sign-in-title"
                    )
                    yield self._title_widget
                    self._subtitle_widget = NoMarkupStatic(
                        "", id="browser-sign-in-subtitle"
                    )
                    yield self._subtitle_widget
                    self._step_widgets = [
                        NoMarkupStatic("", classes="browser-sign-in-step"),
                        NoMarkupStatic("", classes="browser-sign-in-step"),
                        NoMarkupStatic("", classes="browser-sign-in-step"),
                    ]
                    yield from self._step_widgets
                    yield NoMarkupStatic("", id="browser-sign-in-status")
                    yield NoMarkupStatic("", id="browser-sign-in-hint")

    def on_mount(self) -> None:
        provider_name = self.provider.name.capitalize()
        self._subtitle_widget.update(
            f"Continue with {provider_name} to finish setup automatically."
        )
        self._hint_widget = self.query_one("#browser-sign-in-hint", NoMarkupStatic)
        self._status_widget = self.query_one("#browser-sign-in-status", NoMarkupStatic)
        self.state = self._initial_state
        self.watch_state(self.state)
        self.call_after_refresh(self._start_browser_sign_in)

    def on_unmount(self) -> None:
        self._cancel_current_attempt()

    def action_retry(self) -> None:
        if not self.state.running:
            self._start_browser_sign_in()

    def action_manual(self) -> None:
        self._cancel_current_attempt()
        self.app.switch_screen("api_key")

    def action_cancel(self) -> None:
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
        self._active_attempt_number = None
        self._worker = None
        self.app.exit(
            persist_api_key(
                _resolve_onboarding_provider(self.provider),
                api_key,
                entrypoint_metadata=self._entrypoint_metadata,
            )
        )

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
            case BrowserSignInStatus.EXCHANGING:
                state = BrowserSignInViewState(
                    step=BrowserSignInStep.FINISH,
                    message="Finishing setup...",
                    hint=PENDING_HINT,
                    variant="pending",
                    running=True,
                )
            case BrowserSignInStatus.COMPLETED:
                state = BrowserSignInViewState(
                    step=BrowserSignInStep.FINISH,
                    message="You're signed in. Finishing setup...",
                    hint=PENDING_HINT,
                    variant="success",
                    running=True,
                )
            case _:
                return

        self.state = state

    def watch_state(self, state: BrowserSignInViewState) -> None:
        if not self.is_mounted:
            return

        self._status_widget.update(state.message)
        self._status_widget.remove_class("pending", "error", "success")
        self._status_widget.add_class(state.variant)
        self._hint_widget.update(state.hint)

        for index, (widget, description) in enumerate(
            zip(self._step_widgets, STEP_DESCRIPTIONS, strict=True)
        ):
            if index < state.step:
                prefix = "✓"
                widget_class = "done"
            elif index == state.step:
                prefix = "›"
                widget_class = "active"
            else:
                prefix = "·"
                widget_class = "idle"
            widget.update(f"{prefix} {description}")
            widget.remove_class("done", "active", "idle")
            widget.add_class(widget_class)

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
