from __future__ import annotations

from urllib.parse import quote

import pytest
from textual.app import App, ComposeResult
from textual.content import Content

from vibe.cli.textual_ui.widgets.links import LinkStatic
from vibe.cli.textual_ui.widgets.teleport_message import TeleportMessage


def _click_actions(content: object) -> list[str]:
    spans = getattr(content, "spans", [])
    return [
        span.style.meta["@click"]
        for span in spans
        if span.style.meta and "@click" in span.style.meta
    ]


class _TeleportHarness(App):
    def __init__(self, url: str = "https://chat.example.com/code/project/session"):
        super().__init__()
        self.url = url
        self.opened: list[str] = []

    def compose(self) -> ComposeResult:
        yield TeleportMessage()

    def on_mount(self) -> None:
        self.query_one(TeleportMessage).set_complete(self.url)

    def open_url(self, url: str, *, new_tab: bool = True) -> None:
        self.opened.append(url)


def test_completed_teleport_url_is_marked_clickable() -> None:
    url = "https://chat.example.com/code/project/session"
    message = TeleportMessage()
    message.set_complete(url)

    content = message._format_text(message.get_content())

    assert isinstance(content, Content)
    assert content.plain == "Teleported to Vibe Code Web"
    assert _click_actions(content) == [f"open_url('{quote(url, safe='')}')"]


@pytest.mark.asyncio
async def test_completed_teleport_link_action_opens_url() -> None:
    app = _TeleportHarness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.query_one(LinkStatic).action_open_url(quote(app.url, safe=""))
        await pilot.pause(0.1)

    assert app.opened == ["https://chat.example.com/code/project/session"]
