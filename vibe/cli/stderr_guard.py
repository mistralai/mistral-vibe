"""Guard against stray native writes to fd 2 corrupting the Textual TUI."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
import os
import sys


def _cleanup(
    render_file: object | None,
    render_fd: int | None,
    saved_dunder: object,
    saved_stderr: object,
) -> None:
    """Restore sys.stderr / sys.__stderr__ and fd 2, ignoring errors."""
    sys.__stderr__ = saved_dunder  # type: ignore[assignment]
    sys.stderr = saved_stderr  # type: ignore[assignment]
    if render_file is not None:
        try:
            render_file.close()  # type: ignore[union-attr]
        except Exception:
            pass
    if render_fd is not None:
        try:
            os.dup2(render_fd, 2)
        except Exception:
            pass
        finally:
            try:
                os.close(render_fd)
            except Exception:
                pass


@contextmanager
def stderr_guard() -> Generator[None]:
    """Redirect OS-level fd 2 away from the terminal while keeping a
    separate fd for Textual's rendering output.

    Textual renders to ``sys.__stderr__`` (fd 2) and captures Python-level
    ``sys.stderr`` via ``contextlib.redirect_stderr``.  Native C code (e.g.
    macOS ``libsystem_malloc``) that calls ``write(2, …)`` bypasses that
    redirect and corrupts the TUI.

    This guard:

    1. Dups fd 2 to a new fd still pointing at the real terminal.
    2. Redirects fd 2 to ``/dev/null`` (absorbs stray native writes).
    3. Swaps ``sys.__stderr__`` / ``sys.stderr`` to a file object wrapping
       the dup'd fd so Textual keeps rendering to the real terminal.
    4. Restores everything on exit.

    No-op on Windows or when fd 2 is not a tty.
    """
    if sys.platform == "win32" or not _is_stderr_a_tty():
        yield
        return

    render_fd: int | None = None
    render_file = None
    saved_dunder = sys.__stderr__
    saved_stderr = sys.stderr

    try:
        render_fd = os.dup(2)

        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull_fd, 2)
        finally:
            os.close(devnull_fd)

        render_file = os.fdopen(
            render_fd, "w", closefd=False, errors="backslashreplace"
        )

        sys.__stderr__ = render_file  # type: ignore[assignment]
        sys.stderr = render_file
    except Exception:
        _cleanup(render_file, render_fd, saved_dunder, saved_stderr)
        yield
        return

    try:
        yield
    finally:
        try:
            render_file.flush()
        except Exception:
            pass

        _cleanup(render_file, render_fd, saved_dunder, saved_stderr)


def _is_stderr_a_tty() -> bool:
    try:
        return os.isatty(2)
    except OSError:
        return False
