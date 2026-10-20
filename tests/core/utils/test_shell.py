from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys

import pytest

import vibe.core.utils.shell as shell


def test_shell_environment_does_not_invent_locale(monkeypatch) -> None:
    monkeypatch.setattr(shell, "is_windows", lambda: False)
    monkeypatch.delenv("LC_ALL", raising=False)

    environment = shell._shell_environment()

    assert "LC_ALL" not in environment


def test_shell_environment_forces_lc_ctype_and_drops_inherited_lc_all(
    monkeypatch,
) -> None:
    monkeypatch.setattr(shell, "is_windows", lambda: False)
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.delenv("LC_CTYPE", raising=False)

    environment = shell._shell_environment()

    assert "LC_ALL" not in environment
    assert environment["LC_CTYPE"] == "C.UTF-8"


def test_shell_environment_preserves_other_user_locale_categories(monkeypatch) -> None:
    monkeypatch.setattr(shell, "is_windows", lambda: False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LC_TIME", "fr_FR.UTF-8")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")

    environment = shell._shell_environment()

    assert environment["LC_TIME"] == "fr_FR.UTF-8"
    assert environment["LANG"] == "fr_FR.UTF-8"
    assert environment["LC_CTYPE"] == "C.UTF-8"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells only")
@pytest.mark.asyncio
async def test_a_command_runs_without_the_null_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A null device path that does not exist, as after a command
    deleted ``/dev/null``.
    *Do*: Run a command that reads its standard input.
    *Assert*: It runs, and reads empty input instead of waiting for more.
    """
    # Prepare
    monkeypatch.setattr(os, "devnull", str(tmp_path / "missing-null"))

    # Do
    process = await shell.spawn_shell_command("cat; echo done", cwd=tmp_path)
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)

    # Assert
    assert stdout == b"done\n"
    assert process.returncode == 0
