from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import Static

from vibe.cli.textual_ui.app import WHEEL_SCROLL_DURATION, ChatScroll


class _ScrollApp(App[None]):
    CSS_PATH = Path(__file__).parents[3] / "vibe/cli/textual_ui/app.tcss"

    def compose(self) -> ComposeResult:
        with ChatScroll(id="chat"):
            yield Static("\n".join(f"line {i}" for i in range(200)))


def _wheel(
    cls: type[MouseScrollDown | MouseScrollUp], *, ctrl: bool = False
) -> MouseScrollDown | MouseScrollUp:
    return cls(
        widget=None,
        x=0,
        y=0,
        delta_x=0,
        delta_y=0,
        button=0,
        shift=False,
        meta=False,
        ctrl=ctrl,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_cls", "handler_name", "scroller_name"),
    [
        (MouseScrollDown, "_on_mouse_scroll_down", "_scroll_down_for_pointer"),
        (MouseScrollUp, "_on_mouse_scroll_up", "_scroll_up_for_pointer"),
    ],
)
async def test_wheel_scroll_animates_smoothly(
    event_cls: type[MouseScrollDown | MouseScrollUp],
    handler_name: str,
    scroller_name: str,
) -> None:
    app = _ScrollApp()
    async with app.run_test(size=(80, 10)):
        chat = app.query_one("#chat", ChatScroll)
        calls: list[dict[str, object]] = []

        def fake_scroller(**kwargs: object) -> bool:
            calls.append(kwargs)
            return True

        setattr(chat, scroller_name, fake_scroller)
        event = _wheel(event_cls)

        getattr(chat, handler_name)(event)

        assert calls == [
            {"animate": True, "duration": WHEEL_SCROLL_DURATION, "easing": "linear"}
        ]
        # prevent_default breaks the MRO loop so the base instant-jump never runs.
        assert event._no_default_action is True


@pytest.mark.asyncio
async def test_ctrl_wheel_left_to_base_handler() -> None:
    app = _ScrollApp()
    async with app.run_test(size=(80, 10)):
        chat = app.query_one("#chat", ChatScroll)
        calls: list[dict[str, object]] = []

        def fake_scroller(**kwargs: object) -> bool:
            calls.append(kwargs)
            return True

        chat._scroll_down_for_pointer = fake_scroller
        event = MouseScrollDown(
            widget=None,
            x=0,
            y=0,
            delta_x=0,
            delta_y=0,
            button=0,
            shift=False,
            meta=False,
            ctrl=True,
        )

        chat._on_mouse_scroll_down(event)

        assert calls == []
        # Not prevented, so Textual's MRO dispatch still reaches the base handler.
        assert event._no_default_action is False
