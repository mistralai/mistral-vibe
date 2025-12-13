from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vibe.core.tools.base import BaseToolState
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig


class _DummyProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = 0
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_runs_commands_via_git_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    async def fake_exec(*exec_args: str, **kwargs: Any) -> _DummyProc:
        calls["args"] = exec_args
        calls["kwargs"] = kwargs
        return _DummyProc(stdout=b"hi")

    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.asyncio.create_subprocess_exec",
        fake_exec,
    )
    def _fail_shell(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - defensive
        raise AssertionError("shell should not be used")

    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.asyncio.create_subprocess_shell",
        _fail_shell,
    )
    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.is_windows",
        lambda: True,
    )

    config = BashToolConfig(
        use_git_bash_env=True,
        git_bash_path="C:/Git/bin/bash.exe",
        git_bash_prelude="./setup.sh",
    )
    tool = Bash(config=config, state=BaseToolState())

    result = await tool.run(BashArgs(command="echo hi"))

    assert result.stdout.strip() == "hi"
    assert calls["args"] == ("C:/Git/bin/bash.exe", "-lc", "source ./setup.sh && echo hi")
    kwargs = calls["kwargs"]
    assert kwargs["cwd"] == config.effective_workdir
    assert "CI" in kwargs["env"]
    assert kwargs["stdin"] is asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_git_bash_runs_without_prelude(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    async def fake_exec(*exec_args: str, **kwargs: Any) -> _DummyProc:
        calls["args"] = exec_args
        calls["kwargs"] = kwargs
        return _DummyProc()

    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.asyncio.create_subprocess_exec",
        fake_exec,
    )
    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.is_windows",
        lambda: True,
    )

    config = BashToolConfig(use_git_bash_env=True, git_bash_path="C:/Git/bin/bash.exe")
    tool = Bash(config=config, state=BaseToolState())

    await tool.run(BashArgs(command="echo hi"))

    assert calls["args"] == ("C:/Git/bin/bash.exe", "-lc", "echo hi")
