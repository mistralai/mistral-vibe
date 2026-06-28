from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from weakref import WeakKeyDictionary

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Link

from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.windowing.history import build_history_widgets
from vibe.core.types import (
    FileImageSource,
    ImageAttachment,
    InlineImageSource,
    LLMMessage,
    Role,
)


def _att(path: Path, alias: str) -> ImageAttachment:
    path.write_bytes(b"\x89PNG")
    return ImageAttachment(
        source=FileImageSource(path=path), alias=alias, mime_type="image/png"
    )


class _UserMessageApp(App[None]):
    CSS = """
    .user-message-wrapper,
    .user-message-container,
    .user-message-attachments,
    .user-message-attachment-line {
        width: 100%;
        height: auto;
    }

    .user-message-attachment-label,
    .user-message-attachment-link {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, message: UserMessage) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield self._message


@pytest.mark.asyncio
async def test_attachments_render_one_clickable_link_per_image(tmp_path: Path) -> None:
    a = _att(tmp_path / "a.png", "a.png")
    b = _att(tmp_path / "b.png", "b.png")
    app = _UserMessageApp(UserMessage("look", images=[a, b]))
    opened: list[str] = []

    a_uri = (tmp_path / "a.png").as_uri()
    b_uri = (tmp_path / "b.png").as_uri()

    async with app.run_test() as pilot:
        links = list(app.query(Link))
        assert [link.text for link in links] == ["a.png", "b.png"]
        assert [link.url for link in links] == [a_uri, b_uri]

        with patch.object(
            app, "open_url", side_effect=lambda url, **_kwargs: opened.append(url)
        ):
            await pilot.click(links[0])

    assert opened == [a_uri]


@pytest.mark.asyncio
async def test_inline_attachment_renders_label_without_link() -> None:
    att = ImageAttachment(
        source=InlineImageSource(data="aW1n"), alias="pasted.png", mime_type="image/png"
    )
    app = _UserMessageApp(UserMessage("look", images=[att]))

    async with app.run_test():
        assert list(app.query(Link)) == []
        label = app.query_one(".user-message-attachment-link")

    assert isinstance(label, NoMarkupStatic)


@pytest.mark.asyncio
async def test_attachment_link_renders_alias_brackets_literally(tmp_path: Path) -> None:
    att = _att(tmp_path / "shot.png", "weird [bracket].png")
    app = _UserMessageApp(UserMessage("look", images=[att]))

    async with app.run_test():
        link = app.query_one(Link)

    assert link.text == "weird [bracket].png"


@pytest.mark.asyncio
async def test_attachment_link_shortens_home_absolute_alias() -> None:
    path = Path.home() / "Pictures" / "shot.png"
    att = ImageAttachment(
        source=FileImageSource(path=path), alias=str(path), mime_type="image/png"
    )
    app = _UserMessageApp(UserMessage("look", images=[att]))

    async with app.run_test():
        link = app.query_one(Link)

    assert link.text == "~/Pictures/shot.png"
    assert link.url == path.as_uri()


def test_resumed_user_message_with_images_renders_footer(tmp_path: Path) -> None:
    att = _att(tmp_path / "shot.png", "shot.png")
    msg = LLMMessage(role=Role.user, content="look at @shot.png", images=[att])

    widgets = build_history_widgets(
        [msg],
        tool_call_map={},
        start_index=0,
        history_widget_indices=WeakKeyDictionary(),
    )

    assert len(widgets) == 1
    user_widget = widgets[0]
    assert isinstance(user_widget, UserMessage)
    assert user_widget._images == [att]


def test_resumed_user_message_with_images_only_still_mounts(tmp_path: Path) -> None:
    att = _att(tmp_path / "shot.png", "shot.png")
    msg = LLMMessage(role=Role.user, content="", images=[att])

    widgets = build_history_widgets(
        [msg],
        tool_call_map={},
        start_index=0,
        history_widget_indices=WeakKeyDictionary(),
    )

    assert len(widgets) == 1
    assert isinstance(widgets[0], UserMessage)
