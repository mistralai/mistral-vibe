from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.cli.update_notifier.update import (
    do_update,
    force_reinstall_latest,
    is_uv_tool_install,
)


@pytest.fixture
def installed_version(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    def set_version(version: str) -> None:
        monkeypatch.setattr(
            "vibe.cli.update_notifier.update.INSTALLED_VERSION_COMMAND",
            f"echo vibe {version}",
        )

    return set_version


def _python_command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


@pytest.mark.asyncio
async def test_do_update_runs_all_commands_even_when_first_succeeds() -> None:
    mock_process = MagicMock()
    mock_process.communicate = AsyncMock(return_value=(b"vibe 2.0.0\n", b""))
    mock_process.returncode = 0

    with (
        patch(
            "vibe.cli.update_notifier.update.UPDATE_COMMANDS",
            ["command_1", "command_2"],
        ),
        patch(
            "vibe.cli.update_notifier.update.asyncio.create_subprocess_shell",
            return_value=mock_process,
        ) as mock_create,
    ):
        result = await do_update("2.0.0")

    assert result is True
    commands = [call.args[0] for call in mock_create.call_args_list]
    assert commands[:2] == ["command_1", "command_2"]


@pytest.mark.asyncio
async def test_do_update_succeeds_when_installed_version_reaches_latest(
    monkeypatch: pytest.MonkeyPatch, installed_version: Callable[[str], None]
) -> None:
    monkeypatch.setattr(
        "vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["exit 1", "exit 0"]
    )
    installed_version("2.0.0")

    assert await do_update("2.0.0") is True


@pytest.mark.asyncio
async def test_do_update_fails_when_installed_version_stays_old(
    monkeypatch: pytest.MonkeyPatch, installed_version: Callable[[str], None]
) -> None:
    monkeypatch.setattr("vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["exit 0"])
    installed_version("1.0.0")

    assert await do_update("2.0.0") is False


@pytest.mark.asyncio
async def test_do_update_fails_when_installed_version_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["exit 0"])
    monkeypatch.setattr(
        "vibe.cli.update_notifier.update.INSTALLED_VERSION_COMMAND", "exit 1"
    )

    assert await do_update("2.0.0") is False


@pytest.mark.asyncio
async def test_do_update_succeeds_when_commands_fail_but_version_reaches_latest(
    monkeypatch: pytest.MonkeyPatch, installed_version: Callable[[str], None]
) -> None:
    monkeypatch.setattr(
        "vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["exit 1", "exit 1"]
    )
    installed_version("2.0.0")

    assert await do_update("2.0.0") is True


@pytest.mark.asyncio
async def test_force_reinstall_succeeds_once_the_reinstall_installs_latest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    version_file = (tmp_path / "version.txt").as_posix()
    (tmp_path / "version.txt").write_text("vibe 1.0.0", encoding="utf-8")
    monkeypatch.setattr(
        "vibe.cli.update_notifier.update.FORCE_REINSTALL_COMMAND",
        _python_command(
            f"import pathlib; pathlib.Path('{version_file}').write_text('vibe 2.0.0')"
        ),
    )
    monkeypatch.setattr(
        "vibe.cli.update_notifier.update.INSTALLED_VERSION_COMMAND",
        _python_command(
            f"import pathlib; print(pathlib.Path('{version_file}').read_text())"
        ),
    )

    assert await force_reinstall_latest("2.0.0") is True


@pytest.mark.asyncio
async def test_force_reinstall_fails_when_installed_version_stays_old(
    monkeypatch: pytest.MonkeyPatch, installed_version: Callable[[str], None]
) -> None:
    monkeypatch.setattr(
        "vibe.cli.update_notifier.update.FORCE_REINSTALL_COMMAND", "exit 0"
    )
    installed_version("1.0.0")

    assert await force_reinstall_latest("2.0.0") is False


@pytest.mark.parametrize("has_receipt", [True, False])
def test_is_uv_tool_install_follows_the_receipt_in_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, has_receipt: bool
) -> None:
    if has_receipt:
        (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))

    assert is_uv_tool_install() is has_receipt
