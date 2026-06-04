from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session import last_session_pointer


@pytest.fixture
def session_logging(tmp_path: Path) -> SessionLoggingConfig:
    return SessionLoggingConfig(save_dir=str(tmp_path))


def _set_tty(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    monkeypatch.setattr(last_session_pointer, "current_tty_key", lambda: key)


def test_load_returns_none_when_no_tty(
    session_logging: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_tty(monkeypatch, None)
    assert last_session_pointer.load(session_logging) is None


def test_load_returns_none_when_no_pointer_written(
    session_logging: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_tty(monkeypatch, "ttys001")
    assert last_session_pointer.load(session_logging) is None


def test_record_then_load_round_trip(
    session_logging: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_tty(monkeypatch, "ttys001")
    last_session_pointer.record(session_logging, "abc-123")
    assert last_session_pointer.load(session_logging) == "abc-123"


def test_record_skips_when_no_tty(
    session_logging: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_tty(monkeypatch, None)
    last_session_pointer.record(session_logging, "abc-123")
    pointer_dir = Path(session_logging.save_dir) / last_session_pointer.POINTER_DIR_NAME
    assert not pointer_dir.exists()


def test_record_skips_when_logging_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disabled = SessionLoggingConfig(save_dir=str(tmp_path), enabled=False)
    _set_tty(monkeypatch, "ttys001")
    last_session_pointer.record(disabled, "abc-123")
    assert not (tmp_path / last_session_pointer.POINTER_DIR_NAME).exists()


def test_pointers_are_per_tty(
    session_logging: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_tty(monkeypatch, "ttys001")
    last_session_pointer.record(session_logging, "session-a")

    _set_tty(monkeypatch, "ttys002")
    last_session_pointer.record(session_logging, "session-b")
    assert last_session_pointer.load(session_logging) == "session-b"

    _set_tty(monkeypatch, "ttys001")
    assert last_session_pointer.load(session_logging) == "session-a"


def test_record_ignores_empty_session_id(
    session_logging: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_tty(monkeypatch, "ttys001")
    last_session_pointer.record(session_logging, None)
    last_session_pointer.record(session_logging, "")
    assert last_session_pointer.load(session_logging) is None


def test_clear_matching_removes_matching_pointers_only(
    session_logging: SessionLoggingConfig,
) -> None:
    pointer_dir = Path(session_logging.save_dir) / last_session_pointer.POINTER_DIR_NAME
    pointer_dir.mkdir()
    (pointer_dir / "ttys001").write_text("deleted-session\n", encoding="utf-8")
    (pointer_dir / "ttys002").write_text("other-session\n", encoding="utf-8")
    (pointer_dir / "ttys003").write_text("deleted-session\n", encoding="utf-8")
    (pointer_dir / "nested").mkdir()

    last_session_pointer.clear_matching(session_logging, "deleted-session")

    assert not (pointer_dir / "ttys001").exists()
    assert (pointer_dir / "ttys002").read_text(encoding="utf-8") == "other-session\n"
    assert not (pointer_dir / "ttys003").exists()
    assert (pointer_dir / "nested").is_dir()


def test_clear_matching_skips_when_logging_disabled(tmp_path: Path) -> None:
    disabled = SessionLoggingConfig(save_dir=str(tmp_path), enabled=False)
    pointer_dir = tmp_path / last_session_pointer.POINTER_DIR_NAME
    pointer_dir.mkdir()
    pointer_path = pointer_dir / "ttys001"
    pointer_path.write_text("deleted-session\n", encoding="utf-8")

    last_session_pointer.clear_matching(disabled, "deleted-session")

    assert pointer_path.exists()


def test_current_tty_key_returns_none_when_ttyname_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(last_session_pointer.os, "ttyname", raising=False)

    assert last_session_pointer.current_tty_key() is None


def _patch_windows(monkeypatch: pytest.MonkeyPatch, hwnd: int) -> None:
    import ctypes
    from types import SimpleNamespace

    fake_windll = SimpleNamespace(
        kernel32=SimpleNamespace(GetConsoleWindow=lambda: hwnd)
    )
    monkeypatch.setattr(last_session_pointer.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)


def test_current_tty_key_uses_console_hwnd_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_windows(monkeypatch, hwnd=12345)
    assert last_session_pointer.current_tty_key() == "conhost-12345"


def test_current_tty_key_falls_back_to_wt_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_windows(monkeypatch, hwnd=0)
    monkeypatch.setenv("WT_SESSION", "abcd-1234")
    assert last_session_pointer.current_tty_key() == "wt-abcd-1234"


def test_current_tty_key_falls_back_to_ppid_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_windows(monkeypatch, hwnd=0)
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setattr(last_session_pointer.os, "getppid", lambda: 4242)
    assert last_session_pointer.current_tty_key() == "ppid-4242"
