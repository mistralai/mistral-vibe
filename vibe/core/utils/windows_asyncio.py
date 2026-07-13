from __future__ import annotations

from typing import Any

from vibe.core.utils.platform import is_windows

_PATCHED = False


def silence_proactor_transport_teardown_warnings() -> None:
    """Suppress spurious teardown tracebacks from the Windows ProactorEventLoop.

    When subprocess and pipe transports (e.g. stdio MCP servers) are garbage
    collected after their event loop has already closed, their ``__del__``
    builds a ``repr`` that reads a now-closed pipe's file descriptor and raises
    ``ValueError("I/O operation on closed pipe")``. The interpreter merely
    *ignores* the exception, yet still prints the full traceback to stderr,
    which is alarming on a clean ``/exit``. This wraps the offending ``__del__``
    methods to drop that error. No-op off Windows; safe to call more than once.
    """
    global _PATCHED
    if _PATCHED or not is_windows():
        return
    _PATCHED = True

    from asyncio.base_subprocess import BaseSubprocessTransport
    from asyncio.proactor_events import _ProactorBasePipeTransport

    _wrap_del_to_suppress(_ProactorBasePipeTransport)
    _wrap_del_to_suppress(BaseSubprocessTransport)


def _wrap_del_to_suppress(transport_cls: type[Any]) -> None:
    original = transport_cls.__del__

    def _safe_del(self: object) -> None:
        try:
            original(self)
        except (ValueError, OSError):
            pass

    transport_cls.__del__ = _safe_del
