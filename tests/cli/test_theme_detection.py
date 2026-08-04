from __future__ import annotations

from collections.abc import Callable
import ctypes
import ctypes.wintypes
import os
import select
import subprocess
import sys
import threading
import time
import types
from typing import Any

import pytest

from vibe.cli._theme_detection import (
    _classify_osc11_response,
    detect_system_preferred_dark,
    detect_terminal_dark,
    resolve_auto_theme,
    resolve_theme,
)
from vibe.cli.theme import resolve_theme_name


def _completed(
    stdout: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def _clear_cache() -> None:
    resolve_auto_theme.cache_clear()


def test_returns_none_on_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd")
    _clear_cache()
    assert detect_system_preferred_dark() is None


def test_macos_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    _clear_cache()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.subprocess.run",
        lambda *a, **kw: _completed(stdout="Dark\n"),
    )
    assert detect_system_preferred_dark() is True


def test_macos_light(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    _clear_cache()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.subprocess.run",
        lambda *a, **kw: _completed(returncode=1),
    )
    assert detect_system_preferred_dark() is False


def test_macos_subprocess_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    _clear_cache()

    def _raise(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        raise OSError("boom")

    monkeypatch.setattr("vibe.cli._theme_detection.subprocess.run", _raise)
    assert detect_system_preferred_dark() is None


def test_linux_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _clear_cache()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.subprocess.run",
        lambda *a, **kw: _completed(stdout="'prefer-dark'\n"),
    )
    assert detect_system_preferred_dark() is True


def test_linux_light(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _clear_cache()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.subprocess.run",
        lambda *a, **kw: _completed(stdout="'prefer-light'\n"),
    )
    assert detect_system_preferred_dark() is False


def test_linux_default_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _clear_cache()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.subprocess.run",
        lambda *a, **kw: _completed(stdout="'default'\n"),
    )
    assert detect_system_preferred_dark() is None


def test_linux_gsettings_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _clear_cache()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.subprocess.run",
        lambda *a, **kw: _completed(returncode=1),
    )
    assert detect_system_preferred_dark() is None


class _FakeKey:
    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *a: object) -> None:
        pass


class _FakeWinreg(types.ModuleType):
    HKEY_CURRENT_USER = 0

    def __init__(self, query_result: tuple[int, int] | None) -> None:
        super().__init__("winreg")
        self._query_result = query_result

    def OpenKey(self, root: object, path: object) -> _FakeKey:
        if self._query_result is None:
            raise OSError("no key")
        return _FakeKey()

    def QueryValueEx(self, key: object, name: object) -> tuple[int, int]:
        if self._query_result is None:
            raise OSError("no value")
        return self._query_result


def _inject_fake_winreg(
    monkeypatch: pytest.MonkeyPatch, query_result: tuple[int, int] | None
) -> None:
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(query_result))


def test_windows_dark(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _clear_cache()
    _inject_fake_winreg(monkeypatch, (0, 1))
    assert detect_system_preferred_dark() is True


def test_windows_light(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _clear_cache()
    _inject_fake_winreg(monkeypatch, (1, 1))
    assert detect_system_preferred_dark() is False


def test_windows_registry_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _clear_cache()
    _inject_fake_winreg(monkeypatch, None)
    assert detect_system_preferred_dark() is None


def test_windows_winreg_unavailable_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _clear_cache()
    monkeypatch.delitem(sys.modules, "winreg", raising=False)

    def _raise(name: str) -> object:
        raise ModuleNotFoundError("no winreg")

    monkeypatch.setattr("vibe.cli._theme_detection.importlib.import_module", _raise)
    assert detect_system_preferred_dark() is None


class _FakeKernel32:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.GetStdHandle = _FakeWindowsFunction(self._get_std_handle)
        self.WriteFile = _FakeWindowsFunction(self._write_file)

    def _get_std_handle(self, stream: int) -> int:
        assert stream == -12
        return 1

    def _write_file(
        self,
        handle: int,
        data: ctypes.Array[ctypes.c_char],
        size: int,
        written: object,
        overlapped: object,
    ) -> int:
        assert handle == 1
        self.writes.append(data.raw[:size])
        return 1


class _FakeWindowsFunction:
    def __init__(self, function: Callable[..., object]) -> None:
        self._function = function
        self.argtypes: list[Any] | None = None
        self.restype: Any = None

    def __call__(self, *args: object) -> object:
        return self._function(*args)


class _FakeMsvcrt:
    def __init__(self, response: bytes) -> None:
        self._response = list(response)

    def kbhit(self) -> bool:
        return bool(self._response)

    def getch(self) -> bytes:
        return bytes([self._response.pop(0)])


def test_detect_terminal_dark_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    kernel32 = _FakeKernel32()
    monkeypatch.setattr(
        ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), raising=False
    )
    msvcrt = _FakeMsvcrt(b"\x1b]11;rgb:0000/0000/0000\x07")
    monkeypatch.setattr(
        "vibe.cli._theme_detection.importlib.import_module", lambda name: msvcrt
    )

    assert detect_terminal_dark() is True
    assert kernel32.writes == [b"\x1b]11;?\x07"]
    assert kernel32.GetStdHandle.argtypes == [ctypes.wintypes.DWORD]
    assert kernel32.GetStdHandle.restype is ctypes.wintypes.HANDLE
    assert kernel32.WriteFile.argtypes == [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.LPCVOID,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.LPDWORD,
        ctypes.wintypes.LPVOID,
    ]
    assert kernel32.WriteFile.restype is ctypes.wintypes.BOOL


def test_osc11_classify_dark_background() -> None:
    response = b"\x1b]11;rgb:0000/0000/0000\x07"
    assert _classify_osc11_response(response) is True


def test_osc11_classify_light_background() -> None:
    response = b"\x1b]11;rgb:ffff/ffff/ffff\x07"
    assert _classify_osc11_response(response) is False


def test_osc11_classify_dark_hex_short() -> None:
    response = b"\x1b]11;rgb:00/00/00\x07"
    assert _classify_osc11_response(response) is True


def test_osc11_classify_light_hex_short() -> None:
    response = b"\x1b]11;rgb:ff/ff/ff\x07"
    assert _classify_osc11_response(response) is False


def test_osc11_classify_no_match_returns_none() -> None:
    response = b"\x1b]10;rgb:1234/5678/9abc\x07"
    assert _classify_osc11_response(response) is None


def test_osc11_classify_empty_response_returns_none() -> None:
    assert _classify_osc11_response(b"") is None


def test_osc11_classify_partial_response_returns_none() -> None:
    response = b"\x1b]11;rgb:0000/0000"
    assert _classify_osc11_response(response) is None


def test_osc11_classify_st_terminator() -> None:
    response = b"\x1b]11;rgb:1e1e/1e1e/1e1e\x1b\\"
    assert _classify_osc11_response(response) is True


def test_detect_terminal_dark_posix_no_tty_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def _raise(*a: object, **kw: object) -> int:
        raise OSError("no tty")

    monkeypatch.setattr("vibe.cli._theme_detection.os.open", _raise)
    assert detect_terminal_dark() is None


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX pseudo-terminal")
def test_detect_terminal_dark_posix_setraw_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import termios
    import tty

    monkeypatch.setattr(sys, "platform", "linux")
    master_fd, slave_fd = os.openpty()
    probe_fd = os.dup(slave_fd)
    monkeypatch.setattr(
        "vibe.cli._theme_detection.os.open", lambda *args, **kwargs: probe_fd
    )

    def raise_termios_error(*args: object, **kwargs: object) -> None:
        raise termios.error("setraw failed")

    monkeypatch.setattr(tty, "setraw", raise_termios_error)
    try:
        assert detect_terminal_dark() is None
        with pytest.raises(OSError):
            os.fstat(probe_fd)
    finally:
        try:
            os.close(probe_fd)
        except OSError:
            pass
        os.close(master_fd)
        os.close(slave_fd)


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX pseudo-terminal")
def test_detect_terminal_dark_posix_queries_tty_and_classifies_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import termios

    monkeypatch.setattr(sys, "platform", "linux")
    master_fd, slave_fd = os.openpty()
    previous_attributes = termios.tcgetattr(slave_fd)
    monkeypatch.setattr(
        "vibe.cli._theme_detection.os.open", lambda *args, **kwargs: os.dup(slave_fd)
    )
    query = b""
    trailing_input = b"typed while probing\n"

    def respond() -> None:
        nonlocal query
        ready, _, _ = select.select([master_fd], [], [], 1)
        if not ready:
            return
        query = os.read(master_fd, 64)
        os.write(master_fd, b"\x1b]11;rgb:ffff/ffff/ffff\x07" + trailing_input)

    responder = threading.Thread(target=respond)
    responder.start()
    try:
        assert detect_terminal_dark() is False
        assert os.read(slave_fd, len(trailing_input)) == trailing_input
        assert termios.tcgetattr(slave_fd) == previous_attributes
    finally:
        responder.join(timeout=1)
        os.close(master_fd)
        os.close(slave_fd)

    assert not responder.is_alive()
    assert query == b"\x1b]11;?\x07"


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX pseudo-terminal")
def test_detect_terminal_dark_posix_preserves_pending_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    master_fd, slave_fd = os.openpty()
    monkeypatch.setattr(
        "vibe.cli._theme_detection.os.open", lambda *args, **kwargs: os.dup(slave_fd)
    )
    pending_input = b"pasted command\n"
    os.write(master_fd, pending_input)
    # The line discipline echoes the write back out the master asynchronously
    # (with ONLCR turning \n into \r\n); drain it in a loop rather than a single
    # read, which under load returns only the bytes flushed so far.
    expected_echo = b"pasted command\r\n"
    echo = b""
    deadline = time.monotonic() + 1.0
    while len(echo) < len(expected_echo):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([master_fd], [], [], remaining)[0]:
            break
        echo += os.read(master_fd, 64)
    assert echo == expected_echo

    try:
        assert detect_terminal_dark() is None
        assert os.read(slave_fd, len(pending_input)) == pending_input
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_detect_terminal_dark_skipped_inside_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
    _clear_cache()
    assert detect_terminal_dark() is None


def test_detect_terminal_dark_skipped_inside_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("STY", "1234.pts-0.host")
    _clear_cache()
    assert detect_terminal_dark() is None


def test_detect_terminal_dark_skipped_inside_zellij(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("ZELLIJ", "0")
    _clear_cache()
    assert detect_terminal_dark() is None


def test_resolve_auto_theme_uses_terminal_detection_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.cli._theme_detection.detect_terminal_dark", lambda: True)
    monkeypatch.setattr(
        "vibe.cli._theme_detection.detect_system_preferred_dark", lambda: False
    )
    resolve_auto_theme.cache_clear()
    assert resolve_auto_theme() == "ansi-dark"


def test_resolve_auto_theme_falls_back_to_os_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.cli._theme_detection.detect_terminal_dark", lambda: None)
    monkeypatch.setattr(
        "vibe.cli._theme_detection.detect_system_preferred_dark", lambda: False
    )
    resolve_auto_theme.cache_clear()
    assert resolve_auto_theme() == "ansi-light"


def test_resolve_auto_theme_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.cli._theme_detection.detect_terminal_dark", lambda: None)
    monkeypatch.setattr(
        "vibe.cli._theme_detection.detect_system_preferred_dark", lambda: None
    )
    resolve_auto_theme.cache_clear()
    assert resolve_auto_theme() == "ansi-dark"


def test_resolve_auto_theme_terminal_light_overrides_os_dark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.cli._theme_detection.detect_terminal_dark", lambda: False)
    monkeypatch.setattr(
        "vibe.cli._theme_detection.detect_system_preferred_dark", lambda: True
    )
    resolve_auto_theme.cache_clear()
    assert resolve_auto_theme() == "ansi-light"


def test_resolve_theme_resolves_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli._theme_detection.resolve_auto_theme", lambda: "ansi-light"
    )
    assert resolve_theme("auto") == "ansi-light"


def test_resolve_theme_preserves_explicit_theme() -> None:
    assert resolve_theme("gruvbox") == "gruvbox"


def test_resolve_theme_name_accepts_auto() -> None:
    assert resolve_theme_name("auto") == "auto"


def test_resolve_theme_name_returns_auto_for_empty() -> None:
    assert resolve_theme_name("") == "auto"


def test_resolve_theme_name_returns_auto_for_none() -> None:
    assert resolve_theme_name(None) == "auto"


def test_resolve_theme_name_returns_auto_for_unknown() -> None:
    assert resolve_theme_name("nonexistent-theme") == "auto"
