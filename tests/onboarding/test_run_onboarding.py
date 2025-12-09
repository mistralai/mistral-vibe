from __future__ import annotations

from pathlib import Path
import sys
from typing import override

import pytest
from textual.app import App

from vibe.setup import onboarding


class StubApp(App[str | None]):
    def __init__(self, return_value: str | None) -> None:
        super().__init__()
        self._return_value = return_value

    @override
    def run(self, *args: object, **kwargs: object) -> str | None:
        return self._return_value


def _patch_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    env_file = tmp_path / ".env"
    monkeypatch.setattr(onboarding, "GLOBAL_ENV_FILE", env_file, raising=False)
    return env_file


def _exit_raiser(code: int = 0) -> None:
    raise SystemExit(code)


def test_exits_on_cancel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _patch_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "exit", _exit_raiser)

    with pytest.raises(SystemExit) as excinfo:
        onboarding.run_onboarding(StubApp(None))

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Setup cancelled. See you next time!" in out


def test_warns_on_save_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _patch_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "exit", _exit_raiser)

    onboarding.run_onboarding(StubApp("save_error:disk full"))

    out = capsys.readouterr().out
    assert "Could not save API key" in out
    assert "disk full" in out


def test_successfully_completes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _patch_env_file(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "exit", _exit_raiser)

    onboarding.run_onboarding(StubApp("completed"))

    out = capsys.readouterr().out
    assert out == ""
