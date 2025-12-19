from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, mock_open, patch

import pytest
from textual.app import App

from vibe.cli.clipboard import _copy_osc52, copy_selection_to_clipboard


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


@patch("vibe.cli.clipboard._is_windows", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_copy_selection_to_clipboard_success_with_osc52(
    mock_pyperclip_copy: MagicMock,
    mock_osc52_copy: MagicMock,
    mock_is_windows: MagicMock,
    mock_app: MagicMock,
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    copy_selection_to_clipboard(mock_app)

    mock_osc52_copy.assert_called_once_with("selected text")
    mock_pyperclip_copy.assert_not_called()
    mock_app.copy_to_clipboard.assert_not_called()
    mock_app.notify.assert_called_once_with(
        '"selected text" copied to clipboard', severity="information", timeout=2
    )


@patch("vibe.cli.clipboard._is_windows", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_copy_selection_to_clipboard_osc52_fails_success_with_pyperclip(
    mock_pyperclip_copy: MagicMock,
    mock_osc52_copy: MagicMock,
    mock_is_windows: MagicMock,
    mock_app: MagicMock,
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("   selected text  ", None),
    )
    mock_app.query.return_value = [widget]
    mock_osc52_copy.side_effect = Exception("osc52 failed")

    copy_selection_to_clipboard(mock_app)

    mock_osc52_copy.assert_called_once_with("   selected text  ")
    mock_pyperclip_copy.assert_called_once_with("   selected text  ")
    mock_app.notify.assert_called_once_with(
        '"   selected text  " copied to clipboard', severity="information", timeout=2
    )
    mock_app.copy_to_clipboard.assert_not_called()


@patch("vibe.cli.clipboard._is_windows", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_copy_selection_to_clipboard_osc52_and_pyperclip_fail_success_with_app_copy(
    mock_pyperclip_copy: MagicMock,
    mock_osc52_copy: MagicMock,
    mock_is_windows: MagicMock,
    mock_app: MagicMock,
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]
    mock_osc52_copy.side_effect = Exception("osc52 failed")
    mock_pyperclip_copy.side_effect = Exception("pyperclip failed")

    copy_selection_to_clipboard(mock_app)

    mock_osc52_copy.assert_called_once_with("selected text")
    mock_pyperclip_copy.assert_called_once_with("selected text")
    mock_app.copy_to_clipboard.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        '"selected text" copied to clipboard', severity="information", timeout=2
    )


@patch("vibe.cli.clipboard._is_windows", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_copy_selection_to_clipboard_all_methods_fail(
    mock_pyperclip_copy: MagicMock,
    mock_osc52_copy: MagicMock,
    mock_is_windows: MagicMock,
    mock_app: MagicMock,
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]
    mock_osc52_copy.side_effect = Exception("osc52 failed")
    mock_pyperclip_copy.side_effect = Exception("pyperclip failed")
    mock_app.copy_to_clipboard.side_effect = Exception("app copy failed")

    copy_selection_to_clipboard(mock_app)

    mock_osc52_copy.assert_called_once_with("selected text")
    mock_pyperclip_copy.assert_called_once_with("selected text")
    mock_app.copy_to_clipboard.assert_called_once_with("selected text")
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

    with (
        patch("vibe.cli.clipboard._is_windows", return_value=False),
        patch("vibe.cli.clipboard._copy_osc52") as mock_osc52_copy,
    ):
        copy_selection_to_clipboard(mock_app)

        mock_osc52_copy.assert_called_once_with("first selection\nsecond selection")
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

    with (
        patch("vibe.cli.clipboard._is_windows", return_value=False),
        patch("vibe.cli.clipboard._copy_osc52") as mock_osc52_copy,
        patch("vibe.cli.clipboard.pyperclip.copy") as mock_pyperclip_copy,
    ):
        mock_osc52_copy.side_effect = Exception("osc52 failed")
        copy_selection_to_clipboard(mock_app)

        mock_osc52_copy.assert_called_once_with(long_text)
        mock_pyperclip_copy.assert_called_once_with(long_text)
        notification_call = mock_app.notify.call_args
        assert notification_call is not None
        assert '"' in notification_call[0][0]
        assert "copied to clipboard" in notification_call[0][0]
        assert len(notification_call[0][0]) < len(long_text) + 30


@patch("vibe.cli.clipboard._is_windows", return_value=False)
@patch("builtins.open", new_callable=mock_open)
def test_copy_osc52_writes_correct_sequence(
    mock_file: MagicMock, mock_is_windows: MagicMock, monkeypatch: pytest.MonkeyPatch
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


@patch("vibe.cli.clipboard._is_windows", return_value=True)
def test_copy_osc52_raises_on_windows(mock_is_windows: MagicMock) -> None:
    """Test that OSC52 raises an error on Windows."""
    with pytest.raises(OSError, match="OSC52 not supported on Windows"):
        _copy_osc52("test text")


@patch("vibe.cli.clipboard._is_windows", return_value=True)
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_copy_selection_to_clipboard_windows_skips_osc52(
    mock_pyperclip_copy: MagicMock,
    mock_is_windows: MagicMock,
    mock_app: MagicMock,
) -> None:
    """Test that on Windows, OSC52 is skipped and pyperclip is used directly."""
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    with patch("vibe.cli.clipboard._copy_osc52") as mock_osc52_copy:
        copy_selection_to_clipboard(mock_app)

        # OSC52 should not be called on Windows
        mock_osc52_copy.assert_not_called()
        # Pyperclip should be called instead
        mock_pyperclip_copy.assert_called_once_with("selected text")
        mock_app.notify.assert_called_once_with(
            '"selected text" copied to clipboard', severity="information", timeout=2
        )
