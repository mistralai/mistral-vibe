from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, mock_open, patch

import pytest
from textual.app import App

from vibe.cli import clipboard as clipboard_module
from vibe.cli.clipboard import (
    MAX_IMAGE_BYTES,
    _copy_osc52,
    _copy_pbcopy,
    _copy_to_clipboard,
    _copy_wl_copy,
    _copy_xclip,
    _detect_image_format,
    _encode_image_data_url,
    _paste_image_osascript,
    _paste_image_wl_paste,
    _paste_image_xclip,
    _read_clipboard,
    _read_clipboard_image,
    copy_selection_to_clipboard,
    copy_text_to_clipboard,
)


def _real_fd_mkstemp(target: Path) -> tuple[int, str]:
    """Return (real_fd, path) so production code can os.close(fd) safely.

    The real os.close() in production is what plugs the fd leak, so the
    fake must hand out a fd that is actually open.
    """
    return os.open(os.devnull, os.O_RDONLY), str(target)


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


@patch("vibe.cli.clipboard._copy_to_clipboard")
def test_copy_selection_skips_detached_widget_and_collects_valid(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    detached = MockWidgetNoScreen()
    valid = MockWidget(
        text_selection=SimpleNamespace(), get_selection_result=("valid text", None)
    )
    mock_app.query.return_value = [detached, valid]

    result = copy_selection_to_clipboard(mock_app)

    assert result == "valid text"
    mock_copy_to_clipboard.assert_called_once_with("valid text")


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
        "Selection copied to clipboard", severity="information", timeout=2, markup=False
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
            "Selection copied to clipboard",
            severity="information",
            timeout=2,
            markup=False,
        )


@patch("vibe.cli.clipboard._copy_to_clipboard")
def test_copy_text_to_clipboard_success(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    result = copy_text_to_clipboard(
        mock_app, "assistant text", success_message="Agent message copied"
    )

    assert result == "assistant text"
    mock_copy_to_clipboard.assert_called_once_with("assistant text")
    mock_app.notify.assert_called_once_with(
        "Agent message copied", severity="information", timeout=2, markup=False
    )


@patch("vibe.cli.clipboard._copy_to_clipboard")
def test_copy_text_to_clipboard_shows_failure_when_clipboard_unavailable(
    mock_copy_to_clipboard: MagicMock, mock_app: MagicMock
) -> None:
    mock_copy_to_clipboard.side_effect = RuntimeError("All clipboard strategies failed")

    result = copy_text_to_clipboard(mock_app, "assistant text")

    assert result is None
    mock_copy_to_clipboard.assert_called_once_with("assistant text")
    mock_app.notify.assert_called_once_with(
        "Failed to copy - clipboard not available", severity="warning", timeout=3
    )


def test_copy_text_to_clipboard_returns_none_for_empty_text(
    mock_app: MagicMock,
) -> None:
    result = copy_text_to_clipboard(mock_app, "")
    assert result is None
    mock_app.notify.assert_not_called()


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
    mock_run.assert_called_once_with(
        ["pbcopy"], input=b"hello", check=True, stderr=subprocess.DEVNULL
    )


@patch("subprocess.run")
def test_copy_xclip(mock_run: MagicMock) -> None:
    _copy_xclip("hello")
    mock_run.assert_called_once_with(
        ["xclip", "-selection", "clipboard"],
        input=b"hello",
        check=True,
        stderr=subprocess.DEVNULL,
    )


@patch("subprocess.run")
def test_copy_wl_copy(mock_run: MagicMock) -> None:
    _copy_wl_copy("hello")
    mock_run.assert_called_once_with(
        ["wl-copy"], input=b"hello", check=True, stderr=subprocess.DEVNULL
    )


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


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SAMPLE_PNG = PNG_SIGNATURE + b"rest-of-fake-png-bytes"


def test_paste_image_xclip_returns_bytes() -> None:
    completed = SimpleNamespace(returncode=0, stdout=SAMPLE_PNG, stderr=b"")
    with patch("subprocess.run", return_value=completed) as run:
        assert _paste_image_xclip() == SAMPLE_PNG
    run.assert_called_once()
    args = run.call_args.args[0]
    assert args[0] == "xclip"
    assert "image/png" in args


def test_paste_image_xclip_returns_none_on_failure() -> None:
    completed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"err")
    with patch("subprocess.run", return_value=completed):
        assert _paste_image_xclip() is None


def test_paste_image_xclip_returns_none_on_empty_stdout() -> None:
    completed = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    with patch("subprocess.run", return_value=completed):
        assert _paste_image_xclip() is None


def test_paste_image_wl_paste_returns_bytes() -> None:
    completed = SimpleNamespace(returncode=0, stdout=SAMPLE_PNG, stderr=b"")
    with patch("subprocess.run", return_value=completed) as run:
        assert _paste_image_wl_paste() == SAMPLE_PNG
    args = run.call_args.args[0]
    assert args[0] == "wl-paste"
    assert "image/png" in args


def test_paste_image_wl_paste_returns_none_on_failure() -> None:
    completed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
    with patch("subprocess.run", return_value=completed):
        assert _paste_image_wl_paste() is None


def test_paste_image_osascript_roundtrips_tempfile(tmp_path: Path) -> None:
    tmpfile = tmp_path / "clipboard.png"
    tmpfile.write_bytes(SAMPLE_PNG)

    completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    with (
        patch(
            "vibe.cli.clipboard.tempfile.mkstemp",
            side_effect=lambda suffix="": _real_fd_mkstemp(tmpfile),
        ),
        patch("subprocess.run", return_value=completed),
    ):
        assert _paste_image_osascript() == SAMPLE_PNG


def test_paste_image_osascript_returns_none_when_clipboard_has_no_image(
    tmp_path: Path,
) -> None:
    tmpfile = tmp_path / "clipboard.png"

    completed = SimpleNamespace(returncode=0, stdout="no_image\n", stderr="")
    with (
        patch(
            "vibe.cli.clipboard.tempfile.mkstemp",
            side_effect=lambda suffix="": _real_fd_mkstemp(tmpfile),
        ),
        patch("subprocess.run", return_value=completed),
    ):
        assert _paste_image_osascript() is None


def test_paste_image_osascript_returns_none_on_empty_file(tmp_path: Path) -> None:
    tmpfile = tmp_path / "clipboard.png"
    tmpfile.write_bytes(b"")

    completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    with (
        patch(
            "vibe.cli.clipboard.tempfile.mkstemp",
            side_effect=lambda suffix="": _real_fd_mkstemp(tmpfile),
        ),
        patch("subprocess.run", return_value=completed),
    ):
        assert _paste_image_osascript() is None


def test_paste_image_osascript_cleans_tempfile(tmp_path: Path) -> None:
    tmpfile = tmp_path / "clipboard.png"
    tmpfile.write_bytes(SAMPLE_PNG)

    completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    with (
        patch(
            "vibe.cli.clipboard.tempfile.mkstemp",
            side_effect=lambda suffix="": _real_fd_mkstemp(tmpfile),
        ),
        patch("subprocess.run", return_value=completed),
    ):
        _paste_image_osascript()
    assert not tmpfile.exists()


def test_paste_image_osascript_closes_mkstemp_fd(tmp_path: Path) -> None:
    """Regression: discarding the fd from mkstemp leaks one fd per paste."""
    tmpfile = tmp_path / "clipboard.png"
    tmpfile.write_bytes(SAMPLE_PNG)
    issued: list[int] = []

    def fake_mkstemp(suffix: str = "") -> tuple[int, str]:
        fd, path = _real_fd_mkstemp(tmpfile)
        issued.append(fd)
        return fd, path

    completed = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    with (
        patch("vibe.cli.clipboard.tempfile.mkstemp", side_effect=fake_mkstemp),
        patch("subprocess.run", return_value=completed),
    ):
        _paste_image_osascript()

    assert issued, "mkstemp was never called"
    with pytest.raises(OSError):
        os.fstat(issued[0])


def test_read_clipboard_image_picks_first_available_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard_module, "_has_cmd", lambda cmd: cmd == "xclip"
    )
    completed = SimpleNamespace(returncode=0, stdout=SAMPLE_PNG, stderr=b"")
    with patch("subprocess.run", return_value=completed):
        result = _read_clipboard_image()
    assert result == (SAMPLE_PNG, "image/png")


def test_read_clipboard_image_returns_none_when_no_tools_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clipboard_module, "_has_cmd", lambda cmd: False)
    assert _read_clipboard_image() is None


def test_read_clipboard_image_rejects_oversized_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard_module, "_has_cmd", lambda cmd: cmd == "xclip"
    )
    oversized = b"\x00" * (MAX_IMAGE_BYTES + 1)
    completed = SimpleNamespace(returncode=0, stdout=oversized, stderr=b"")
    with patch("subprocess.run", return_value=completed):
        assert _read_clipboard_image() is None


def test_read_clipboard_image_falls_through_on_reader_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard_module,
        "_has_cmd",
        lambda cmd: cmd in {"xclip", "wl-paste"},
    )

    def raise_then_succeed(*args: object, **kwargs: object) -> SimpleNamespace:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("xclip blew up")
        return SimpleNamespace(returncode=0, stdout=SAMPLE_PNG, stderr=b"")

    call_count = {"n": 0}
    with patch("subprocess.run", side_effect=raise_then_succeed):
        result = _read_clipboard_image()
    assert result == (SAMPLE_PNG, "image/png")


def test_encode_image_data_url_round_trip() -> None:
    url = _encode_image_data_url(SAMPLE_PNG, "image/png")
    assert url.startswith("data:image/png;base64,")
    decoded = base64.b64decode(url.split(",", 1)[1])
    assert decoded == SAMPLE_PNG


SAMPLE_JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-rest"
SAMPLE_GIF = b"GIF89a" + b"gif-rest"
SAMPLE_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"webp-rest"
SAMPLE_TIFF_LE = b"II*\x00" + b"tiff-rest"
SAMPLE_TIFF_BE = b"MM\x00*" + b"tiff-rest"


@pytest.mark.parametrize(
    "data,expected",
    [
        (SAMPLE_PNG, "image/png"),
        (SAMPLE_JPEG, "image/jpeg"),
        (SAMPLE_GIF, "image/gif"),
        (SAMPLE_WEBP, "image/webp"),
        (SAMPLE_TIFF_LE, None),
        (SAMPLE_TIFF_BE, None),
        (b"\x00\x00\x00\x00", None),
        (b"", None),
    ],
)
def test_detect_image_format(data: bytes, expected: str | None) -> None:
    assert _detect_image_format(data) == expected


def test_read_clipboard_image_returns_jpeg_label_for_jpeg_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """osascript falls back to JPEG when PNG isn't on the clipboard; the helper
    must report image/jpeg, not image/png; Mistral decodes by media type.
    """
    monkeypatch.setattr(clipboard_module, "_has_cmd", lambda cmd: cmd == "xclip")
    completed = SimpleNamespace(returncode=0, stdout=SAMPLE_JPEG, stderr=b"")
    with patch("subprocess.run", return_value=completed):
        result = _read_clipboard_image()
    assert result == (SAMPLE_JPEG, "image/jpeg")


def test_read_clipboard_image_rejects_tiff(monkeypatch: pytest.MonkeyPatch) -> None:
    """TIFF is not in Mistral's supported formats; reject rather than mislabel."""
    monkeypatch.setattr(clipboard_module, "_has_cmd", lambda cmd: cmd == "xclip")
    completed = SimpleNamespace(returncode=0, stdout=SAMPLE_TIFF_LE, stderr=b"")
    with patch("subprocess.run", return_value=completed):
        assert _read_clipboard_image() is None
