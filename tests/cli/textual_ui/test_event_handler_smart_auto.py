from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock

import pytest

from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.types import SmartAutoDecisionEvent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "reason", "css_class"),
    [
        ("ALLOW", "This only reads project files.", "allow"),
        ("ASK", "This would remove test coverage.", "ask"),
    ],
)
async def test_smart_auto_decision_is_visible(
    verdict: Literal["ALLOW", "ASK"], reason: str, css_class: str
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(
        SmartAutoDecisionEvent(
            tool_name="bash", tool_call_id="call-1", verdict=verdict, reason=reason
        )
    )

    assert mount_callback.await_args is not None
    widget = mount_callback.await_args.args[0]
    assert isinstance(widget, NoMarkupStatic)
    assert str(widget.render()) == f"Careful YOLO: {verdict} — {reason}"
    assert widget.has_class("smart-auto-decision")
    assert widget.has_class(css_class)
