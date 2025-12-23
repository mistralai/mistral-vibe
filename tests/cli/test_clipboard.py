from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, mock_open, patch

import pyperclip
import pytest
from textual.app import App

from vibe.cli.clipboard import (
    _copy_osc52,
    _copy_wayland_clipboard,
    _copy_x11_clipboard,
    _get_copy_fns,
    copy_selection_to_clipboard,
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


@pytest.fixture
def mock_app() -> App:
    app = MagicMock(spec=App)
    app.query = MagicMock(return_value=[])
    app.notify = MagicMock()
    app.copy_to_clipboard = MagicMock()
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
    ],
)
def test_copy_selection_to_clipboard_no_notification(
    mock_app: MagicMock, widgets: list[MockWidget], description: str
) -> None:
    if description == "widget without text_selection attr":
        del widgets[0].text_selection
    mock_app.query.return_value = widgets

    copy_selection_to_clipboard(mock_app)
    mock_app.notify.assert_not_called()


@patch("vibe.cli.clipboard._get_copy_fns")
def test_copy_selection_to_clipboard_success(
    mock_get_copy_fns: MagicMock, mock_app: MagicMock
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    mock_copy_fn = MagicMock()
    mock_get_copy_fns.return_value = [mock_copy_fn]

    copy_selection_to_clipboard(mock_app)

    mock_copy_fn.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        '"selected text" copied to clipboard', severity="information", timeout=2
    )


@patch("vibe.cli.clipboard._get_copy_fns")
def test_copy_selection_to_clipboard_tries_all(
    mock_get_copy_fns: MagicMock, mock_app: MagicMock
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    fn_1 = MagicMock(side_effect=Exception("failed"))
    fn_2 = MagicMock()
    fn_3 = MagicMock()
    mock_get_copy_fns.return_value = [fn_1, fn_2, fn_3]

    copy_selection_to_clipboard(mock_app)

    fn_1.assert_called_once_with("selected text")
    fn_2.assert_called_once_with("selected text")
    fn_3.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        '"selected text" copied to clipboard', severity="information", timeout=2
    )


@patch("vibe.cli.clipboard._get_copy_fns")
def test_copy_selection_to_clipboard_all_methods_fail(
    mock_get_copy_fns: MagicMock, mock_app: MagicMock
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    failing_fn1 = MagicMock(side_effect=Exception("failed 1"))
    failing_fn2 = MagicMock(side_effect=Exception("failed 2"))
    failing_fn3 = MagicMock(side_effect=Exception("failed 3"))
    mock_get_copy_fns.return_value = [failing_fn1, failing_fn2, failing_fn3]

    copy_selection_to_clipboard(mock_app)

    failing_fn1.assert_called_once_with("selected text")
    failing_fn2.assert_called_once_with("selected text")
    failing_fn3.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        "Failed to copy - no clipboard method available", severity="warning", timeout=3
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

    with patch("vibe.cli.clipboard._get_copy_fns") as mock_get_copy_fns:
        mock_copy_fn = MagicMock()
        mock_get_copy_fns.return_value = [mock_copy_fn]
        copy_selection_to_clipboard(mock_app)

        mock_copy_fn.assert_called_once_with("first selection\nsecond selection")
        mock_app.notify.assert_called_once_with(
            '"first selection⏎second selection" copied to clipboard',
            severity="information",
            timeout=2,
        )


def test_copy_selection_to_clipboard_preview_shortening(mock_app: MagicMock) -> None:
    long_text = "a" * 100
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=(long_text, None)
    )
    mock_app.query.return_value = [widget]

    with patch("vibe.cli.clipboard._get_copy_fns") as mock_get_copy_fns:
        mock_copy_fn = MagicMock()
        mock_get_copy_fns.return_value = [mock_copy_fn]
        copy_selection_to_clipboard(mock_app)

        mock_copy_fn.assert_called_once_with(long_text)
        notification_call = mock_app.notify.call_args
        assert notification_call is not None
        assert '"' in notification_call[0][0]
        assert "copied to clipboard" in notification_call[0][0]
        assert len(notification_call[0][0]) < len(long_text) + 30


@patch("builtins.open", new_callable=mock_open)
def test_copy_osc52_writes_correct_sequence(
    mock_file: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    test_text = "héllo wörld 🎉"

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


@patch("vibe.cli.clipboard.subprocess.run")
def test_copy_x11_clipboard(mock_subprocess: MagicMock) -> None:
    test_text = "test text"

    _copy_x11_clipboard(test_text)

    mock_subprocess.assert_called_once_with(
        ["xclip", "-selection", "clipboard"],
        input=test_text.encode("utf-8"),
        check=True,
    )


@patch("vibe.cli.clipboard.subprocess.run")
def test_copy_wayland_clipboard(mock_subprocess: MagicMock) -> None:
    test_text = "test text"

    _copy_wayland_clipboard(test_text)

    mock_subprocess.assert_called_once_with(
        ["wl-copy"], input=test_text.encode("utf-8"), check=True
    )


@patch("vibe.cli.clipboard.shutil.which")
def test_get_copy_fns_no_system_tools(mock_which: MagicMock, mock_app: App) -> None:
    mock_which.return_value = None

    copy_fns = _get_copy_fns(mock_app)

    assert len(copy_fns) == 3
    assert copy_fns[0] == _copy_osc52
    assert copy_fns[1] == pyperclip.copy
    assert copy_fns[2] == mock_app.copy_to_clipboard


@patch("vibe.cli.clipboard.shutil.which")
def test_get_copy_fns_with_xclip(mock_which: MagicMock, mock_app: App) -> None:
    def which_side_effect(cmd: str) -> str | None:
        return "/usr/bin/xclip" if cmd == "xclip" else None

    mock_which.side_effect = which_side_effect

    copy_fns = _get_copy_fns(mock_app)

    assert len(copy_fns) == 4
    assert copy_fns[0] == _copy_x11_clipboard
    assert copy_fns[1] == _copy_osc52
    assert copy_fns[2] == pyperclip.copy
    assert copy_fns[3] == mock_app.copy_to_clipboard


@patch("vibe.cli.clipboard.shutil.which")
def test_get_copy_fns_with_wl_copy(mock_which: MagicMock, mock_app: App) -> None:
    def which_side_effect(cmd: str) -> str | None:
        return "/usr/bin/wl-copy" if cmd == "wl-copy" else None

    mock_which.side_effect = which_side_effect

    copy_fns = _get_copy_fns(mock_app)

    assert len(copy_fns) == 4
    assert copy_fns[0] == _copy_wayland_clipboard
    assert copy_fns[1] == _copy_osc52
    assert copy_fns[2] == pyperclip.copy
    assert copy_fns[3] == mock_app.copy_to_clipboard


@patch("vibe.cli.clipboard.shutil.which")
def test_get_copy_fns_with_both_system_tools(
    mock_which: MagicMock, mock_app: App
) -> None:
    def which_side_effect(cmd: str) -> str | None:
        match cmd:
            case "wl-copy":
                return "/usr/bin/wl-copy"
            case "xclip":
                return "/usr/bin/xclip"
            case _:
                return None

    mock_which.side_effect = which_side_effect

    copy_fns = _get_copy_fns(mock_app)

    assert len(copy_fns) == 5
    # xclip is checked last, so it's added last and ends up first in the list
    assert copy_fns[0] == _copy_x11_clipboard
    # wl-copy is checked first, so it's added before xclip
    assert copy_fns[1] == _copy_wayland_clipboard
    assert copy_fns[2] == _copy_osc52
    assert copy_fns[3] == pyperclip.copy
    assert copy_fns[4] == mock_app.copy_to_clipboard
