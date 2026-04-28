from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from vibe.cli.stderr_guard import stderr_guard

_FORCE_ACTIVE = patch("vibe.cli.stderr_guard._is_stderr_a_tty", return_value=True)


@pytest.mark.skipif(sys.platform == "win32", reason="fd redirect is Unix-only")
class TestStderrGuard:
    def test_fd2_redirected_to_devnull_inside_guard(self) -> None:
        with _FORCE_ACTIVE:
            with stderr_guard():
                devnull_stat = os.stat(os.devnull)
                fd2_stat = os.fstat(2)
                assert fd2_stat.st_rdev == devnull_stat.st_rdev

    def test_fd2_restored_after_guard(self) -> None:
        stat_before = os.fstat(2)
        with _FORCE_ACTIVE:
            with stderr_guard():
                stat_inside = os.fstat(2)
                assert stat_inside.st_ino != stat_before.st_ino
        stat_after = os.fstat(2)
        assert stat_after.st_ino == stat_before.st_ino

    def test_sys_stderr_restored_after_exit(self) -> None:
        original_stderr = sys.stderr
        original_dunder = sys.__stderr__
        with _FORCE_ACTIVE:
            with stderr_guard():
                assert sys.__stderr__ is not original_dunder
        assert sys.stderr is original_stderr
        assert sys.__stderr__ is original_dunder

    def test_restores_stderr_when_render_flush_raises_value_error(self) -> None:
        original_stderr = sys.stderr
        original_dunder = sys.__stderr__
        stat_before = os.fstat(2)

        with _FORCE_ACTIVE:
            with stderr_guard():
                assert sys.__stderr__ is not None
                sys.__stderr__.close()

        stat_after = os.fstat(2)
        assert stat_after.st_ino == stat_before.st_ino
        assert sys.stderr is original_stderr
        assert sys.__stderr__ is original_dunder

    def test_render_file_is_writable_inside_guard(self) -> None:
        with _FORCE_ACTIVE:
            with stderr_guard():
                assert sys.__stderr__ is not None
                assert sys.__stderr__.writable()

    def test_noop_when_stderr_is_not_a_tty(self) -> None:
        original_stderr = sys.stderr
        with patch("vibe.cli.stderr_guard._is_stderr_a_tty", return_value=False):
            with stderr_guard():
                assert sys.stderr is original_stderr

    def test_native_write_to_fd2_goes_to_devnull(self) -> None:
        with _FORCE_ACTIVE:
            with stderr_guard():
                # Should not raise; bytes vanish into /dev/null.
                written = os.write(2, b"stray MallocStackLogging message\n")
                assert written > 0
