from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, mock_open, patch

import pytest
import base64
from textual.app import App

from vibe.cli.clipboard import _copy_osc52, copy_selection_to_clipboard
# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# No-op / empty cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "widgets",
    [
        [],
        [MockWidget(text_selection=None)],
        [MockWidget()],
        [
            MockWidget(
                text_selection=SimpleNamespace(),
                get_selection_raises=ValueError("selection error"),
            )
        ],
        [MockWidget(text_selection=SimpleNamespace(), get_selection_result=None)],
        [MockWidget(text_selection=SimpleNamespace(), get_selection_result=("   ", None))],
    ],
)
def test_copy_selection_to_clipboard_noop_cases(
    mock_app: MagicMock, widgets: list[MockWidget]
) -> None:
    mock_app.query.return_value = widgets
    copy_selection_to_clipboard(mock_app)
    mock_app.notify.assert_not_called()


# ---------------------------------------------------------------------------
# REMOTE MODE
# ---------------------------------------------------------------------------

@patch("vibe.cli.clipboard._is_wayland", return_value=False)
@patch("vibe.cli.clipboard._is_x11", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_remote_osc52_fails_fallback_to_pyperclip(
    mock_pyperclip_copy: MagicMock,
    mock_osc52_copy: MagicMock,
    _mock_is_x11: MagicMock,
    _mock_is_wayland: MagicMock,
    mock_app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REAL fallback test:
    remote env, no GUI → osc52 fails → textual fails → pyperclip succeeds
    """
    monkeypatch.setenv("VIBE_CLIPBOARD_MODE", "remote")

    widget = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("selected text", None),
    )
    mock_app.query.return_value = [widget]
    mock_osc52_copy.side_effect = Exception("osc52 failed")
    mock_app.copy_to_clipboard.side_effect = Exception("textual failed")

    copy_selection_to_clipboard(mock_app)

    mock_osc52_copy.assert_called_once_with("selected text")
    mock_pyperclip_copy.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once()

@patch("vibe.cli.clipboard._is_wayland", return_value=False)
@patch("vibe.cli.clipboard._is_x11", return_value=False)
@patch("vibe.cli.clipboard._copy_osc52")
@patch("vibe.cli.clipboard.pyperclip.copy")
def test_remote_all_methods_fail(
    mock_pyperclip_copy: MagicMock,
    mock_osc52_copy: MagicMock,
    _mock_is_x11: MagicMock,
    _mock_is_wayland: MagicMock,
    mock_app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote env, no GUI,
    all clipboard methods fail → warning
    """
    monkeypatch.setenv("VIBE_CLIPBOARD_MODE", "remote")

    widget = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("selected text", None),
    )
    mock_app.query.return_value = [widget]
    mock_osc52_copy.side_effect = Exception("osc52 failed")
    mock_app.copy_to_clipboard.side_effect = Exception("textual failed")
    mock_pyperclip_copy.side_effect = Exception("pyperclip failed")

    copy_selection_to_clipboard(mock_app)

    mock_app.notify.assert_called_once_with(
        "Failed to copy - no clipboard method available",
        severity="warning",
        timeout=3,
    )


# ---------------------------------------------------------------------------
# LOCAL MODE
# ---------------------------------------------------------------------------

@patch("vibe.cli.clipboard._is_wayland", return_value=False)
@patch("vibe.cli.clipboard._is_x11", return_value=False)
def test_local_success_with_app_copy(
    _mock_is_x11: MagicMock,
    _mock_is_wayland: MagicMock,
    mock_app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path local:
    local env → textual app clipboard
    """
    monkeypatch.setenv("VIBE_CLIPBOARD_MODE", "local")

    widget = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("selected text", None),
    )
    mock_app.query.return_value = [widget]

    copy_selection_to_clipboard(mock_app)

    mock_app.copy_to_clipboard.assert_called_once_with("selected text")
    mock_app.notify.assert_called_once_with(
        '"selected text" copied to clipboard',
        severity="information",
        timeout=2,
    )

# ---------------------------------------------------------------------------
# Multiple widgets
# ---------------------------------------------------------------------------

@patch("vibe.cli.clipboard._is_wayland", return_value=False)
@patch("vibe.cli.clipboard._is_x11", return_value=False)
def test_copy_selection_multiple_widgets(
    _mock_is_x11: MagicMock,
    _mock_is_wayland: MagicMock,
    mock_app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_CLIPBOARD_MODE", "local")
    widget1 = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("first", None),
    )
    widget2 = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=("second", None),
    )
    mock_app.query.return_value = [widget1, widget2]

    copy_selection_to_clipboard(mock_app)

    mock_app.copy_to_clipboard.assert_called_once_with("first\nsecond")
    mock_app.notify.assert_called_once()


# ---------------------------------------------------------------------------
# Preview shortening
# ---------------------------------------------------------------------------

@patch("vibe.cli.clipboard._is_wayland", return_value=False)
@patch("vibe.cli.clipboard._is_x11", return_value=False)
def test_preview_shortening(
    _mock_is_x11: MagicMock,
    _mock_is_wayland: MagicMock,
    mock_app: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_CLIPBOARD_MODE", "local")
    long_text = "a" * 200
    widget = MockWidget(
        text_selection=SimpleNamespace(),
        get_selection_result=(long_text, None),
    )
    mock_app.query.return_value = [widget]

    copy_selection_to_clipboard(mock_app)

    notify_text = mock_app.notify.call_args[0][0]
    assert "copied to clipboard" in notify_text
    assert len(notify_text) < len(long_text)


@patch("builtins.open", new_callable=mock_open)
def test_copy_osc52_writes_correct_sequence(
    mock_file: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    test_text = "héllo wörld "

    _copy_osc52(test_text)

    encoded = base64.b64encode(test_text.encode("utf-8")).decode("ascii")
    expected_seq = f"\033]52;c;{encoded}\a"
    mock_file.assert_called_once_with("/dev/tty", "w")
    handle = mock_file()
    handle.write.assert_called_once_with(expected_seq)
    handle.flush.assert_called_once()

