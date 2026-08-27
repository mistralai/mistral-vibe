from __future__ import annotations

from typing import cast

import pytest

from vibe.core.tools.builtins.experimental_bash import TerminalSessionManager
from vibe.core.tools.terminal_runtime import TerminalRuntime


class _FakeTerminalManager:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.closed = False

    def reset(self, *, clear_logs: bool) -> None:
        assert clear_logs is False
        self.closed = True
        if self.error is not None:
            raise self.error


def test_close_attempts_every_terminal_manager_after_failure() -> None:
    first = _FakeTerminalManager(RuntimeError("first failed"))
    second = _FakeTerminalManager()
    runtime = TerminalRuntime()
    runtime._managers = {
        ("posix", "bash"): cast(TerminalSessionManager, first),
        ("powershell", "powershell"): cast(TerminalSessionManager, second),
    }

    with pytest.raises(RuntimeError, match="first failed"):
        runtime.close()

    assert first.closed is True
    assert second.closed is True
    assert runtime._managers == {}
