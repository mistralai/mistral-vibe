from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from vibe.cli.cli import _handle_forced_upgrade, run_cli
from vibe.cli.entrypoint import parse_arguments
from vibe.cli.update_notifier import FileSystemUpdateCacheRepository, UpdateCache
from vibe.cli.update_notifier.update import UpdateAvailability


class TestUpgradeFlagArgumentParsing:
    def test_upgrade_flag_is_parsed_correctly(self) -> None:
        args = parse_arguments(["--upgrade"])
        assert args.upgrade is True

    def test_upgrade_flag_defaults_to_false(self) -> None:
        args = parse_arguments([])
        assert args.upgrade is False

    def test_upgrade_flag_with_other_args(self) -> None:
        args = parse_arguments(["--upgrade", "--setup"])
        assert args.upgrade is True
        assert args.setup is True


class TestHandleForcedUpgrade:
    @pytest.fixture
    def repository(self, tmp_path: Path) -> FileSystemUpdateCacheRepository:
        return FileSystemUpdateCacheRepository(base_path=tmp_path)

    def test_upgrade_when_already_up_to_date(
        self, repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that when no update is available, it prints up-to-date message and exits 0."""
        with (
            patch(
                "vibe.cli.cli.get_update_if_available",
                return_value=None,
            ),
            patch("vibe.cli.cli.__version__", "1.0.0"),
            pytest.raises(SystemExit) as excinfo,
        ):
            _handle_forced_upgrade()

        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "✔ Vibe is up to date (v1.0.0)" in captured.out

    def test_upgrade_when_update_available_and_successful(
        self, repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that when update is available and successful, it prints success and exits 0."""
        update_availability = UpdateAvailability(
            latest_version="2.0.0", should_notify=True
        )
        with (
            patch(
                "vibe.cli.cli.get_update_if_available",
                return_value=update_availability,
            ),
            patch("vibe.cli.cli.do_update", return_value=True),
            patch("vibe.cli.cli.__version__", "1.0.0"),
            pytest.raises(SystemExit) as excinfo,
        ):
            _handle_forced_upgrade()

        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "New version available: 1.0.0 → 2.0.0" in captured.out
        assert "✔ Vibe was updated from 1.0.0 to 2.0.0" in captured.out

    def test_upgrade_when_update_available_but_fails(
        self, repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that when update is available but fails, it prints error and exits 1."""
        update_availability = UpdateAvailability(
            latest_version="2.0.0", should_notify=True
        )
        with (
            patch(
                "vibe.cli.cli.get_update_if_available",
                return_value=update_availability,
            ),
            patch("vibe.cli.cli.do_update", return_value=False),
            patch("vibe.cli.cli.__version__", "1.0.0"),
            pytest.raises(SystemExit) as excinfo,
        ):
            _handle_forced_upgrade()

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "New version available: 1.0.0 → 2.0.0" in captured.out
        assert "✗ Vibe could not be updated automatically" in captured.out

    def test_upgrade_when_update_check_fails(
        self, repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that when update check fails, it prints error and exits 1."""
        with (
            patch(
                "vibe.cli.cli.get_update_if_available",
                side_effect=Exception("Network error"),
            ),
            patch("vibe.cli.cli.__version__", "1.0.0"),
            pytest.raises(SystemExit) as excinfo,
        ):
            _handle_forced_upgrade()

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "✗ Failed to check for updates" in captured.out

    def test_upgrade_when_update_fails_with_exception(
        self, repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that when update process fails with exception, it prints error and exits 1."""
        update_availability = UpdateAvailability(
            latest_version="2.0.0", should_notify=True
        )
        with (
            patch(
                "vibe.cli.cli.get_update_if_available",
                return_value=update_availability,
            ),
            patch("vibe.cli.cli.do_update", side_effect=Exception("Update failed")),
            patch("vibe.cli.cli.__version__", "1.0.0"),
            pytest.raises(SystemExit) as excinfo,
        ):
            _handle_forced_upgrade()

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert "✗ Vibe could not be updated automatically" in captured.out


class TestRunCliWithUpgradeFlag:
    def test_run_cli_with_upgrade_flag_calls_handle_forced_upgrade(
        self, tmp_path: Path
    ) -> None:
        """Test that run_cli calls _handle_forced_upgrade when --upgrade flag is set."""
        args = parse_arguments(["--upgrade"])
        
        with (
            patch("vibe.cli.cli._handle_forced_upgrade") as mock_handle_upgrade,
            patch("vibe.cli.cli.load_dotenv_values"),
            patch("vibe.cli.cli.bootstrap_config_files"),
            pytest.raises(SystemExit),
        ):
            run_cli(args)
        
        mock_handle_upgrade.assert_called_once()

    def test_run_cli_without_upgrade_flag_does_not_call_handle_forced_upgrade(
        self, tmp_path: Path
    ) -> None:
        """Test that run_cli does not call _handle_forced_upgrade when --upgrade flag is not set."""
        args = parse_arguments([])
        
        with (
            patch("vibe.cli.cli._handle_forced_upgrade") as mock_handle_upgrade,
            patch("vibe.cli.cli.load_dotenv_values"),
            patch("vibe.cli.cli.bootstrap_config_files"),
            patch("vibe.cli.cli.load_config_or_exit"),
        ):
            # This will fail due to missing config, but we just want to check that
            # _handle_forced_upgrade is not called
            try:
                run_cli(args)
            except:
                pass
        
        mock_handle_upgrade.assert_not_called()