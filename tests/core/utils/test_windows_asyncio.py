from __future__ import annotations

import pytest

from vibe.core.utils import windows_asyncio
from vibe.core.utils.windows_asyncio import (
    _wrap_del_to_suppress,
    silence_proactor_transport_teardown_warnings,
)


class _RaisingTransport:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.armed = True
        self.del_called = False

    def __del__(self) -> None:
        self.del_called = True
        if self.armed:
            raise self._exc


def test_wrap_del_suppresses_value_error():
    _wrap_del_to_suppress(_RaisingTransport)
    obj = _RaisingTransport(ValueError("I/O operation on closed pipe"))
    obj.__del__()
    assert obj.del_called


def test_wrap_del_suppresses_os_error():
    _wrap_del_to_suppress(_RaisingTransport)
    obj = _RaisingTransport(OSError("bad fd"))
    obj.__del__()
    assert obj.del_called


def test_wrap_del_lets_other_exceptions_propagate():
    _wrap_del_to_suppress(_RaisingTransport)
    obj = _RaisingTransport(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        obj.__del__()
    obj.armed = False


def test_silence_is_noop_off_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(windows_asyncio, "_PATCHED", False)
    monkeypatch.setattr(windows_asyncio, "is_windows", lambda: False)
    silence_proactor_transport_teardown_warnings()
    assert windows_asyncio._PATCHED is False
