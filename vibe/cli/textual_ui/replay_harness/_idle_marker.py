from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import (
    visible_screen_stack,  # pyright: ignore[reportPrivateImportUsage]
)

from vibe.cli.autocompletion.path_completion import has_pending_completions
from vibe.cli.textual_ui.replay_harness._drain import drain_pumps
from vibe.cli.textual_ui.replay_harness._protocol import (
    HOLD_KEY,
    MARKER,
    RELEASE_KEY,
    replaying,
    settle_busy,
)
from vibe.cli.textual_ui.widgets.diff_rendering import has_pending_diff_renders
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp

if TYPE_CHECKING:
    from vibe.cli.textual_ui.app import VibeApp

# Worker groups whose answers repaint an auth bottom-app (`ConnectorAuthApp`,
# `MCPOAuthApp`); the marker waits for them so it never freezes the placeholder.
_AUTH_WORKER_GROUPS = frozenset({"auth_url", "connector_refresh", "mcp_oauth_login"})


class ReplayIdleMarker:
    """Idle marker for the main app, emitted once its frame stops moving."""

    def __init__(self, app: VibeApp) -> None:
        self._app = app
        self._enabled = replaying()
        self._settle_busy = settle_busy()
        self._startup_settled = False
        self._emitted = False
        self._held = False
        self._settle_pending = False
        self._generation = 0

    def consume_batch_key(self, key: str) -> bool:
        """Hold or release marker emission for a batched input step."""
        if not self._enabled or key not in {HOLD_KEY, RELEASE_KEY}:
            return False
        self._held = key == HOLD_KEY
        if not self._held:
            self.maybe_emit()
        return True

    def startup_settled(self) -> None:
        self._startup_settled = True
        self.maybe_emit()

    def rearm(self) -> None:
        # A fresh settle episode, so a key-driven repaint (`/theme`) emits again.
        if self._enabled:
            self._generation += 1
            self._emitted = False

    def rearm_after_busy_event(self) -> None:
        """Settle one gated streaming event without changing ordinary replay timing."""
        if self._settle_busy:
            self.rearm()
            self.maybe_emit()

    def maybe_emit(self) -> None:
        # `on_idle` means the queue drained, not that deferred layout settled.
        if not self._enabled or not self._startup_settled or self._held:
            return
        if self._still_moving():
            self._emitted = False
            return
        if self._emitted or self._settle_pending:
            return
        self._settle_pending = True
        self._schedule_settle(self._generation, self._signature())

    def _schedule_settle(self, generation: int, previous: object) -> None:
        def paint() -> None:
            # A repaint may reuse the scrollbar's stale render cache, and its
            # geometry reactives (`window_virtual_size`, `position`) only sync
            # during layout: sync them from the chat's authoritative state so a
            # later scrollbar grab never computes against stale values.
            chat = self._app._chat_widget
            chat._scroll_update(chat.virtual_size)
            # `call_after_refresh` needs a clean screen; flush now, not a tick later.
            self._flush_frame()
            self._app.call_after_refresh(self._settle, generation, previous)

        drain_pumps(self._app, paint)

    def _settle(self, generation: int, previous: object) -> None:
        # Final only once the frame has not moved and the compositor owes no paint.
        if generation != self._generation:
            self._settle_pending = False
            self.maybe_emit()
            return
        if self._emitted or self._held or self._still_moving():
            self._settle_pending = False
            return
        current = self._signature()
        if current == previous and (self._settle_busy or self._screen_quiescent()):
            self._settle_pending = False
            self._emitted = True
            # Passes paint incrementally; the terminal only holds a full frame.
            self._app.refresh()
            self._flush_frame()
            self._write()
            return
        self._schedule_settle(generation, current)

    def _flush_frame(self) -> None:
        try:
            self._app.screen._on_timer_update()
        except Exception:
            pass

    def _off_thread_work(self) -> bool:
        # Worker-thread work leaves the pumps empty, so ask each owner directly.
        return (
            has_pending_completions()
            or has_pending_diff_renders()
            or self._app._agent_switch_active
            or self._app.startup_notices_pending()
            # A resume preview owes a debounce timer or a history fetch; both land
            # after the queue drains, so wait for them like Rust's in-flight gate.
            or self._app.resume_preview_pending()
            # The projection flips `turn_active` on arrival, so the app reads idle
            # while the event is still on its way to the UI.
            or self._events_in_transit()
            or self._auth_worker_pending()
        )

    def _still_moving(self) -> bool:
        # Any signal that the frame has not settled yet.
        return (
            self._off_thread_work()
            or self._auto_scrolling()
            or self._scroll_animating()
            or self._theme_preview_pending()
            or (self._app._is_busy() and not self._settle_busy)
        )

    def _theme_preview_pending(self) -> bool:
        # The theme picker debounces the highlighted theme's preview; the frame
        # is stable while the timer runs, but the theme has not applied yet.
        try:
            picker = self._app.query_one(ThemePickerApp)
        except Exception:
            return False
        return picker._pending_preview is not None

    def _scroll_animating(self) -> bool:
        # A starved animation timer repaints nothing between two signature
        # probes, so the frame can look stable while `scroll_y` is still
        # easing toward `scroll_target_y`; settling then captures mid-flight
        # and leaves the scrollbar's reactives stale for the next mouse step.
        # The target is unclamped (bottom-pinning overshoots `max_scroll_y`),
        # so compare against the reachable position it eases to.
        try:
            chat = self._app._chat_widget
        except Exception:
            return False
        target = max(0.0, min(chat.scroll_target_y, chat.max_scroll_y))
        return round(chat.scroll_y) != round(target)

    def _auto_scrolling(self) -> bool:
        # Textual keeps the select-autoscroll timer live until mouse release, so
        # suppress the marker while the transcript is still moving toward a
        # boundary; once clamped it settles deterministically, matching Rust.
        try:
            screen = self._app.screen
            chat = self._app._chat_widget
        except Exception:
            return False
        if getattr(screen, "_auto_select_scroll_timer", None) is None:
            return False
        return 0 < chat.scroll_y < chat.max_scroll_y

    def _auth_worker_pending(self) -> bool:
        # Auth bottom-apps read the auth URL, refresh the connector, or log into
        # an MCP server in workers; none touch the screen until its RPC answers.
        return any(
            worker.group in _AUTH_WORKER_GROUPS and not worker.is_finished
            for worker in self._app.workers
        )

    def _events_in_transit(self) -> bool:
        # Cleared before the handler runs, so a UI waiting on the user still emits.
        server = self._app._app_server
        return server is not None and server.events_in_transit

    def _signature(self) -> object:
        try:
            chat = self._app._chat_widget
        except Exception:
            return None
        return (chat.scroll_target_y, self._frame())

    def _frame(self) -> object:
        # The only signal for a widget-local repaint, e.g. an OptionList highlight.
        try:
            visible_screen_stack.set(self._app._background_screens)
            return self._app.screen._compositor.render_strips()
        except Exception:
            return None

    def _screen_quiescent(self) -> bool:
        # Repaint flags are excluded here: the blinking caret dirties them.
        try:
            screen = self._app.screen
        except Exception:
            return True
        return not (
            screen._layout_required
            or screen._scroll_required
            or screen._recompose_required
            or screen._callbacks
        )

    def _write(self) -> None:
        driver = self._app._driver
        if driver is None:
            return
        driver.write(MARKER)
        driver.flush()
