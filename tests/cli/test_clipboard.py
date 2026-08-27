from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, mock_open, patch

import pytest
from textual.app import App

from vibe.cli.clipboard import (
    NATIVE_COPY_HINT,
    ClipboardCopyResult,
    _copy_native,
    _copy_osc52,
    copy_selection_to_clipboard,
    copy_text_to_clipboard,
    copy_to_clipboard,
)


class MockWidget:
    def __init__(
        self,
        text_selection: object | None = None,
        get_selection_result: tuple[str, object] | None = None,
        get_selection_raises: Exception | None = None,
    ) -> None:
        self.text_selection = text_selection
        self._get_selection_result = get_selection_result
        self._get_selection_raises = get_selection_raises

    def get_selection(self, selection: object) -> tuple[str, object]:
        if self._get_selection_raises:
            raise self._get_selection_raises
        if self._get_selection_result is None:
            return ("", None)
        return self._get_selection_result


class MockWidgetNoScreen:
    @property
    def text_selection(self) -> object:
        raise RuntimeError("node has no screen")


@pytest.fixture
def mock_app() -> App:
    app = MagicMock(spec=App)
    app.query = MagicMock(return_value=[])
    app.notify = MagicMock()
    return cast(App, app)


@pytest.mark.parametrize(
    "widgets,description",
    [
        ([], "no widgets"),
        ([MockWidget(text_selection=None)], "no selection"),
        ([MockWidget()], "widget without text_selection attr"),
        (
            [
                MockWidget(
                    text_selection=SimpleNamespace(),
                    get_selection_raises=ValueError("Error getting selection"),
                )
            ],
            "get_selection raises",
        ),
        (
            [MockWidget(text_selection=SimpleNamespace(), get_selection_result=None)],
            "empty result",
        ),
        (
            [
                MockWidget(
                    text_selection=SimpleNamespace(), get_selection_result=("   ", None)
                )
            ],
            "empty text",
        ),
        ([MockWidgetNoScreen()], "widget with no screen (text_selection raises)"),
    ],
)
def test_copy_selection_to_clipboard_no_notification(
    mock_app: MagicMock, widgets: list[MockWidget], description: str
) -> None:
    if description == "widget without text_selection attr":
        del widgets[0].text_selection
    mock_app.query.return_value = widgets

    result = copy_selection_to_clipboard(mock_app)
    assert result is None
    mock_app.notify.assert_not_called()


@patch("vibe.cli.clipboard.copy_to_clipboard", return_value=True)
def test_copy_selection_skips_detached_widget_and_collects_valid(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    detached = MockWidgetNoScreen()
    valid = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("valid text", None)
    )
    mock_app.query.return_value = [detached, valid]

    result = copy_selection_to_clipboard(mock_app)

    assert result == ClipboardCopyResult(text="valid text", verified=True)
    mock_copy_to_clipboard.assert_called_once_with("valid text")


@patch("vibe.cli.clipboard.copy_to_clipboard", return_value=True)
def test_copy_selection_to_clipboard_success(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    result = copy_selection_to_clipboard(mock_app)

    assert result == ClipboardCopyResult(text="selected text", verified=True)
    mock_copy_to_clipboard.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        "Selection copied to clipboard", severity="information", timeout=2, markup=False
    )


def test_copy_selection_to_clipboard_multiple_widgets(mock_app: MagicMock) -> None:
    widget1 = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("first selection", None)
    )
    widget2 = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("second selection", None),
    )
    widget3 = MockWidget(text_selection=None)
    mock_app.query.return_value = [widget1, widget2, widget3]

    with patch(
        "vibe.cli.clipboard.copy_to_clipboard", return_value=True
    ) as mock_copy_to_clipboard:
        result = copy_selection_to_clipboard(mock_app)

        assert result == ClipboardCopyResult(
            text="first selection\nsecond selection", verified=True
        )
        mock_copy_to_clipboard.assert_called_once_with(
            "first selection\nsecond selection"
        )
        mock_app.notify.assert_called_once_with(
            "Selection copied to clipboard",
            severity="information",
            timeout=2,
            markup=False,
        )


@pytest.mark.asyncio
async def test_copy_selection_reconstructs_nested_list_indentation() -> None:
    # End-to-end against a real Textual Markdown widget: nested list items sit
    # further right than their parents, and that indentation must survive copy
    # (VIBE-3663).
    from textual.app import App as TextualApp, ComposeResult
    from textual.widgets import Markdown

    md = "1. First item\n2. Second item\n   - nested bullet A\n   - nested bullet B\n"

    class _App(TextualApp):
        def compose(self) -> ComposeResult:
            yield Markdown(md)

    app = _App()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.screen.text_select_all()
        await pilot.pause()
        with patch("vibe.cli.clipboard.copy_to_clipboard", return_value=True):
            result = copy_selection_to_clipboard(app, show_toast=False)

    assert result == ClipboardCopyResult(
        text=(
            " 1.  First item\n"
            " 2.  Second item\n"
            "    •  nested bullet A\n"
            "    •  nested bullet B"
        ),
        verified=True,
    )


@patch("vibe.cli.clipboard.copy_to_clipboard", return_value=True)
def test_copy_text_to_clipboard_success(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    result = copy_text_to_clipboard(
        mock_app, "assistant text", success_message="Agent message copied"
    )

    assert result == ClipboardCopyResult(text="assistant text", verified=True)
    mock_copy_to_clipboard.assert_called_once_with("assistant text")
    mock_app.notify.assert_called_once_with(
        "Agent message copied", severity="information", timeout=2, markup=False
    )


@patch("vibe.cli.clipboard.copy_to_clipboard", return_value=False)
def test_copy_text_to_clipboard_returns_unverified_result(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    result = copy_text_to_clipboard(mock_app, "assistant text", show_toast=False)

    assert result == ClipboardCopyResult(text="assistant text", verified=False)
    mock_copy_to_clipboard.assert_called_once_with("assistant text")
    mock_app.notify.assert_not_called()


@patch("vibe.cli.clipboard.copy_to_clipboard", return_value=False)
def test_copy_text_to_clipboard_appends_hint_to_toast_when_unverified(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    result = copy_text_to_clipboard(
        mock_app, "assistant text", success_message="Agent message copied"
    )

    assert result == ClipboardCopyResult(text="assistant text", verified=False)
    mock_app.notify.assert_called_once_with(
        f"Agent message copied · {NATIVE_COPY_HINT}",
        severity="information",
        timeout=6,
        markup=False,
    )


@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard._copy_native")
def test_copy_to_clipboard_returns_false_for_empty_text(
    copy_native: MagicMock, copy_osc52: MagicMock
) -> None:
    assert copy_to_clipboard("") is False
    copy_native.assert_not_called()
    copy_osc52.assert_not_called()


def test_copy_text_to_clipboard_returns_none_for_empty_text(
    mock_app: MagicMock,
) -> None:
    result = copy_text_to_clipboard(mock_app, "")
    assert result is None
    mock_app.notify.assert_not_called()


@patch("pyperclip.paste", return_value="hello")
@patch("pyperclip.copy")
def test_copy_native_verifies_exact_readback(
    copy_mock: MagicMock, paste_mock: MagicMock
) -> None:
    assert _copy_native("hello") is True
    copy_mock.assert_called_once_with("hello")
    paste_mock.assert_called_once_with()


@patch("pyperclip.paste", return_value="line one\r\nline two")
@patch("pyperclip.copy")
def test_copy_native_verifies_when_only_newlines_differ(
    copy_mock: MagicMock, paste_mock: MagicMock
) -> None:
    assert _copy_native("line one\nline two") is True
    copy_mock.assert_called_once_with("line one\nline two")
    paste_mock.assert_called_once_with()


@patch("pyperclip.paste", return_value="old value")
@patch("pyperclip.copy")
def test_copy_native_returns_unverified_when_readback_differs(
    copy_mock: MagicMock, paste_mock: MagicMock
) -> None:
    assert _copy_native("hello") is False
    copy_mock.assert_called_once_with("hello")
    paste_mock.assert_called_once_with()


@patch("pyperclip.paste", side_effect=RuntimeError("unreadable"))
@patch("pyperclip.copy")
def test_copy_native_returns_unverified_when_readback_fails(
    copy_mock: MagicMock, paste_mock: MagicMock
) -> None:
    assert _copy_native("hello") is False
    copy_mock.assert_called_once_with("hello")
    paste_mock.assert_called_once_with()


@patch("vibe.cli.clipboard._is_ssh_session", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard._copy_native", return_value=True)
def test_copy_to_clipboard_also_emits_osc52_when_native_verified(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is True
    copy_native.assert_called_once_with("hello")
    copy_osc52.assert_called_once_with("hello")


@patch("vibe.cli.clipboard._is_ssh_session", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52", side_effect=OSError("no tty"))
@patch("vibe.cli.clipboard._copy_native", return_value=True)
def test_copy_to_clipboard_stays_verified_when_bonus_osc52_fails(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is True
    copy_native.assert_called_once_with("hello")
    copy_osc52.assert_called_once_with("hello")


@patch("vibe.cli.clipboard._is_ssh_session", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard._copy_native", return_value=False)
def test_copy_to_clipboard_falls_back_to_unverified_osc52(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is False
    copy_native.assert_called_once_with("hello")
    copy_osc52.assert_called_once_with("hello")


@patch("vibe.cli.clipboard._is_ssh_session", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard._copy_native", side_effect=RuntimeError("unavailable"))
def test_copy_to_clipboard_uses_osc52_when_native_copy_fails(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is False
    copy_native.assert_called_once_with("hello")
    copy_osc52.assert_called_once_with("hello")


@patch("vibe.cli.clipboard._is_ssh_session", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52", side_effect=OSError("no tty"))
@patch("vibe.cli.clipboard._copy_native", side_effect=RuntimeError("unavailable"))
def test_copy_to_clipboard_returns_false_when_native_and_osc52_fail(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is False
    copy_native.assert_called_once_with("hello")
    copy_osc52.assert_called_once_with("hello")


@patch("vibe.cli.clipboard._is_ssh_session", return_value=True)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard._copy_native")
def test_copy_to_clipboard_uses_only_osc52_in_ssh_session(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is False
    copy_native.assert_not_called()
    copy_osc52.assert_called_once_with("hello")


@patch("vibe.cli.clipboard._is_ssh_session", return_value=True)
@patch("vibe.cli.clipboard._copy_osc52", side_effect=OSError("no tty"))
@patch("vibe.cli.clipboard._copy_native")
def test_copy_to_clipboard_returns_false_in_ssh_when_osc52_fails(
    copy_native: MagicMock, copy_osc52: MagicMock, is_ssh: MagicMock
) -> None:
    assert copy_to_clipboard("hello") is False
    copy_native.assert_not_called()
    copy_osc52.assert_called_once_with("hello")


@patch("builtins.open", new_callable=mock_open)
def test_copy_osc52_writes_correct_sequence(
    mock_file: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    test_text = "hello world"

    _copy_osc52(test_text)

    encoded = base64.b64encode(test_text.encode("utf-8")).decode("ascii")
    expected_seq = f"\033]52;c;{encoded}\a"
    mock_file.assert_called_once_with("/dev/tty", "w")
    handle = mock_file()
    handle.write.assert_called_once_with(expected_seq)
    handle.flush.assert_called_once()


@patch("builtins.open", new_callable=mock_open)
def test_copy_osc52_with_tmux(
    mock_file: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMUX", "1")
    test_text = "test text"

    _copy_osc52(test_text)

    encoded = base64.b64encode(test_text.encode("utf-8")).decode("ascii")
    expected_seq = f"\033Ptmux;\033\033]52;c;{encoded}\a\033\\"
    handle = mock_file()
    handle.write.assert_called_once_with(expected_seq)


@patch("builtins.open", new_callable=mock_open)
def test_copy_osc52_unicode(
    mock_file: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    test_text = "hello world"

    _copy_osc52(test_text)

    encoded = base64.b64encode(test_text.encode("utf-8")).decode("ascii")
    expected_seq = f"\033]52;c;{encoded}\a"
    handle = mock_file()
    handle.write.assert_called_once_with(expected_seq)
