from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, mock_open, patch

import pytest
from textual.app import App

from vibe.cli.clipboard import (
    _copy_osc52,
    _copy_pbcopy,
    _copy_to_clipboard,
    _copy_wl_copy,
    _copy_xclip,
    _read_clipboard,
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

    result = copy_selection_to_clipboard(mock_app)
    assert result is None
    mock_app.notify.assert_not_called()


@patch("vibe.cli.clipboard._copy_to_clipboard")
def test_copy_selection_to_clipboard_success(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]

    result = copy_selection_to_clipboard(mock_app)

    assert result == "selected text"
    mock_copy_to_clipboard.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        '"selected text" copied to clipboard',
        severity="information",
        timeout=2,
        markup=False,
    )


@patch("vibe.cli.clipboard._copy_to_clipboard")
def test_copy_selection_to_clipboard_shows_failure_when_all_strategies_raise(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    """When _copy_to_clipboard raises (all strategies failed), user sees 'Failed to copy' toast."""
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("selected text", None)
    )
    mock_app.query.return_value = [widget]
    mock_copy_to_clipboard.side_effect = RuntimeError("All clipboard strategies failed")

    result = copy_selection_to_clipboard(mock_app)

    assert result is None
    mock_copy_to_clipboard.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        "Failed to copy - clipboard not available", severity="warning", timeout=3
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

    with patch("vibe.cli.clipboard._copy_to_clipboard") as mock_copy_to_clipboard:
        result = copy_selection_to_clipboard(mock_app)

        assert result == "first selection\nsecond selection"
        mock_copy_to_clipboard.assert_called_once_with(
            "first selection\nsecond selection"
        )
        mock_app.notify.assert_called_once_with(
            '"first selection\u23cesecond selection" copied to clipboard',
            severity="information",
            timeout=2,
            markup=False,
        )


def test_copy_selection_to_clipboard_preview_shortening(mock_app: MagicMock) -> None:
    long_text = "a" * 100
    widget = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=(long_text, None)
    )
    mock_app.query.return_value = [widget]

    with patch("vibe.cli.clipboard._copy_to_clipboard") as mock_copy_to_clipboard:
        result = copy_selection_to_clipboard(mock_app)
        assert result == long_text

        mock_copy_to_clipboard.assert_called_once_with(long_text)
        notification_call = mock_app.notify.call_args
        assert notification_call is not None
        assert '"' in notification_call[0][0]
        assert "copied to clipboard" in notification_call[0][0]
        assert len(notification_call[0][0]) < len(long_text) + 30


def test_copy_to_clipboard_stops_after_verified_copy() -> None:
    """Stops iterating once _read_clipboard confirms the text landed."""
    mock_first = MagicMock()
    mock_second = MagicMock()

    with (
        patch("vibe.cli.clipboard._COPY_METHODS", [mock_first, mock_second]),
        patch("vibe.cli.clipboard._read_clipboard", return_value="hello"),
    ):
        _copy_to_clipboard("hello")

    mock_first.assert_called_once_with("hello")
    mock_second.assert_not_called()


def test_copy_to_clipboard_tries_all_when_verify_fails() -> None:
    """Tries all strategies when _read_clipboard never confirms."""
    mock_first = MagicMock()
    mock_second = MagicMock()

    with (
        patch("vibe.cli.clipboard._COPY_METHODS", [mock_first, mock_second]),
        patch("vibe.cli.clipboard._read_clipboard", return_value=None),
    ):
        _copy_to_clipboard("hello")

    mock_first.assert_called_once_with("hello")
    mock_second.assert_called_once_with("hello")


def test_copy_to_clipboard_raises_when_all_strategies_raise() -> None:
    """RuntimeError is raised when every strategy fails."""
    mock_osc52 = MagicMock(side_effect=OSError("no tty"))
    mock_pyperclip = MagicMock(side_effect=RuntimeError("pyperclip unavailable"))

    with (
        patch("vibe.cli.clipboard._COPY_METHODS", [mock_osc52, mock_pyperclip]),
        pytest.raises(RuntimeError, match="All clipboard strategies failed"),
    ):
        _copy_to_clipboard("anything")


def test_read_clipboard_returns_first_successful_reader() -> None:
    mock_reader = MagicMock(return_value="hello")
    mock_reader2 = MagicMock(side_effect=RuntimeError("no clipboard"))
    with patch(
        "vibe.cli.clipboard._READ_CLIPBOARD_METHODS", [mock_reader, mock_reader2]
    ):
        assert _read_clipboard() == "hello"
    mock_reader.assert_called_once()
    mock_reader2.assert_not_called()


def test_read_clipboard_falls_through_on_failure() -> None:
    failing = MagicMock(side_effect=RuntimeError("no clipboard"))
    with patch("vibe.cli.clipboard._READ_CLIPBOARD_METHODS", [failing]):
        assert _read_clipboard() is None


def test_read_clipboard_skips_failing_reader() -> None:
    failing = MagicMock(side_effect=RuntimeError("broken"))
    working = MagicMock(return_value="hello")
    with patch("vibe.cli.clipboard._READ_CLIPBOARD_METHODS", [failing, working]):
        assert _read_clipboard() == "hello"
    working.assert_called_once()


@patch("subprocess.run")
def test_copy_pbcopy(mock_run: MagicMock) -> None:
    _copy_pbcopy("hello")
    mock_run.assert_called_once_with(["pbcopy"], input=b"hello", check=True)


@patch("subprocess.run")
def test_copy_xclip(mock_run: MagicMock) -> None:
    _copy_xclip("hello")
    mock_run.assert_called_once_with(
        ["xclip", "-selection", "clipboard"], input=b"hello", check=True
    )


@patch("subprocess.run")
def test_copy_wl_copy(mock_run: MagicMock) -> None:
    _copy_wl_copy("hello")
    mock_run.assert_called_once_with(["wl-copy"], input=b"hello", check=True)


def test_copy_methods_includes_available_commands() -> None:
    """_COPY_METHODS is built at import time using _has_cmd; re-import with mocked shutil.which."""
    import importlib

    import vibe.cli.clipboard as mod

    with patch(
        "shutil.which",
        side_effect=lambda cmd: "/usr/bin/xclip" if cmd == "xclip" else None,
    ):
        importlib.reload(mod)
        assert mod._copy_xclip in mod._COPY_METHODS
        assert mod._copy_pbcopy not in mod._COPY_METHODS
        assert mod._copy_wl_copy not in mod._COPY_METHODS

    importlib.reload(mod)


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
