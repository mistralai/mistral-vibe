from __future__ import annotations

from pathlib import Path
from weakref import WeakKeyDictionary

from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.cli.textual_ui.windowing.history import build_history_widgets
from vibe.core.types import FileImageSource, ImageAttachment, LLMMessage, Role


def _att(path: Path, alias: str) -> ImageAttachment:
    path.write_bytes(b"\x89PNG")
    return ImageAttachment(
        source=FileImageSource(path=path), alias=alias, mime_type="image/png"
    )


def test_attachments_footer_singular(tmp_path: Path) -> None:
    att = _att(tmp_path / "shot.png", "shot.png")

    rendered = UserMessage._format_attachments_footer([att])

    assert "attached image:" in rendered
    assert '[link="file://' in rendered
    assert "]shot.png[/link]" in rendered


def test_attachments_footer_plural(tmp_path: Path) -> None:
    a = _att(tmp_path / "a.png", "a.png")
    b = _att(tmp_path / "b.png", "b.png")

    rendered = UserMessage._format_attachments_footer([a, b])

    assert "attached images:" in rendered
    assert rendered.count('[link="file://') == 2
    assert "a.png" in rendered
    assert "b.png" in rendered


def test_attachments_footer_escapes_alias_brackets(tmp_path: Path) -> None:
    att = _att(tmp_path / "shot.png", "weird [bracket].png")

    rendered = UserMessage._format_attachments_footer([att])

    # Rich's escape() turns "[" into "\[".
    assert "\\[bracket]" in rendered


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
