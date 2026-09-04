from __future__ import annotations

from vibe.cli.textual_ui.widgets.loading import (
    DEFAULT_LOADING_STATUS,
    INTERRUPTING_LOADING_STATUS,
    THINKING_LOADING_STATUS,
    LoadingWidget,
)


def test_interrupting_status_sticks_against_late_streaming_updates() -> None:
    """Once interrupting, late streaming status updates must not overwrite it.

    A turn keeps streaming until the cancel propagates, and the event handler
    drives set_status("Thinking"/"Generating") on those events. Without a latch
    they clobber the "Interrupting" label, so the interrupt looks ignored.
    """
    widget = LoadingWidget()
    widget.set_status(INTERRUPTING_LOADING_STATUS)

    widget.set_status(THINKING_LOADING_STATUS)
    widget.set_status(DEFAULT_LOADING_STATUS)
    widget.set_status("Reading file")

    assert widget._base_status == INTERRUPTING_LOADING_STATUS


def test_status_updates_apply_before_interrupting() -> None:
    widget = LoadingWidget()
    widget.set_status(THINKING_LOADING_STATUS)
    assert widget._base_status == THINKING_LOADING_STATUS
    widget.set_status(INTERRUPTING_LOADING_STATUS)
    assert widget._base_status == INTERRUPTING_LOADING_STATUS


def test_action_required_status_holds_while_preserving_latest_progress() -> None:
    widget = LoadingWidget(status="Running command")

    widget.begin_action_required("Waiting for approval to run command")
    widget.set_status(DEFAULT_LOADING_STATUS)

    assert widget.base_status == "Waiting for approval to run command"
    assert widget._pause_start is not None

    widget.end_action_required()

    assert widget.base_status == DEFAULT_LOADING_STATUS
    assert widget._pause_start is None


def test_next_action_required_status_does_not_reset_saved_progress() -> None:
    widget = LoadingWidget(status="Running command")

    widget.begin_action_required("Waiting for first approval")
    widget.begin_action_required("Waiting for second approval")
    widget.end_action_required()

    assert widget.base_status == "Running command"


def test_queue_hint_includes_steer_shortcut() -> None:
    widget = LoadingWidget()
    widget.set_queue_count(2)

    hint = widget._format_hint(10)

    assert "Enter" in hint
    assert "to steer" in hint
    assert "to cancel last queued message" in hint


def test_hint_without_queue_omits_steer_shortcut() -> None:
    widget = LoadingWidget()

    hint = widget._format_hint(10)

    assert "to steer" not in hint
