from __future__ import annotations

from unittest.mock import AsyncMock
from weakref import WeakKeyDictionary

import pytest

from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import PublicCheckpointEntry, PublicEntryGenerationStatus
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.model_change import ModelChangeMessage
from vibe.cli.textual_ui.windowing.history import (
    build_history_widgets,
    history_entry_renders_widget,
)


def _model_change_entry() -> PublicCheckpointEntry:
    return PublicCheckpointEntry(
        id="checkpoint-model-change-1",
        session_id="session-1",
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        kind="model_change",
        message="Model changed to gpt-5.6-sol",
        details={"model": "gpt-5.6-sol"},
    )


@pytest.mark.asyncio
async def test_a_model_change_is_shown_in_the_conversation() -> None:
    """*Prepare*: A live CLI event handler.
    *Do*: Deliver the checkpoint the Harness writes when the model moves.
    *Assert*: The conversation gains a line naming the new model. The handler
    raises on an entry it does not recognise, so before this arm `/model` under
    the Unified Harness killed the event-listening worker.
    """
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback,
        get_tools_collapsed=lambda: False,
        get_show_thinking=lambda: True,
    )

    await handler.handle_event(HistoryEntryAdded(_model_change_entry()))

    mounted = [call.args[0] for call in mount_callback.await_args_list]
    assert [
        widget.get_content()
        for widget in mounted
        if isinstance(widget, ModelChangeMessage)
    ] == ["Model changed to gpt-5.6-sol"]


def test_a_model_change_is_shown_again_when_the_session_is_reopened() -> None:
    """*Prepare*: A transcript page holding a model change, as a resume reads it.
    *Do*: Build its widgets.
    *Assert*: The line comes back. The entry is durable precisely so that it
    does, and a replay that skipped it would show the transcript the marker was
    added to prevent.
    """
    entry = _model_change_entry()

    assert history_entry_renders_widget(entry) is True
    widgets = build_history_widgets(
        [entry],
        start_index=0,
        history_widget_indices=WeakKeyDictionary(),
        tools_collapsed=False,
    )

    assert [
        widget.get_content()
        for widget in widgets
        if isinstance(widget, ModelChangeMessage)
    ] == ["Model changed to gpt-5.6-sol"]
