from __future__ import annotations

from textual.content import Content
from textual.widgets import Static

from vibe.app_server.models import EffectCallDisplay, GenericEffectDetail
from vibe.cli.textual_ui.widgets.tool_widgets import (
    GenericToolResultWidget,
    get_result_widget,
)


def test_generic_tool_result_preserves_multiline_fields() -> None:
    detail = GenericEffectDetail(
        tool_name="skill",
        display=EffectCallDisplay(summary="Loading skill", status_text="Loading skill"),
    )
    widget = get_result_widget(
        detail,
        {
            "name": "review",
            "content": "first instruction\nsecond instruction\nthird instruction",
            "skillDir": "/tmp/review",
            "metadata": {"source": "local"},
        },
        True,
        "Loaded skill: review",
    )

    assert isinstance(widget, GenericToolResultWidget)
    detail_widget = next(
        child for child in widget.compose() if isinstance(child, Static)
    )
    rendered = detail_widget.render()
    assert isinstance(rendered, Content)
    assert rendered.plain == (
        "name: review\n"
        "content: first instruction\n"
        "second instruction\n"
        "third instruction\n"
        "skillDir: /tmp/review\n"
        "metadata: {'source': 'local'}"
    )
