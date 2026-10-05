from __future__ import annotations

import asyncio
from pathlib import Path
import time
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe import __version__
from vibe.cli.cli import _maybe_run_startup_update_prompt, _run_check_upgrade
from vibe.cli.update_notifier import (
    FileSystemUpdateCacheRepository,
    Update,
    UpdateCache,
    UpdateGatewayCause,
    UpdateGatewayError,
)
from vibe.setup.update_prompt.update_prompt_dialog import (
    UpdatePromptMode,
    UpdatePromptResult,
)


class _BrokenRepository:
    async def get(self) -> UpdateCache | None:
        raise OSError("disk on fire")

    async def set(self, update_cache: UpdateCache) -> None:
        raise OSError("disk on fire")


@pytest.fixture
def repository(tmp_path: Path) -> FileSystemUpdateCacheRepository:
    return FileSystemUpdateCacheRepository(base_path=tmp_path)


def _write_pending_update(
    repository: FileSystemUpdateCacheRepository, version: str
) -> None:
    asyncio.run(
        repository.set(
            UpdateCache(latest_version=version, stored_at_timestamp=int(time.time()))
        )
    )


def test_no_op_when_update_checks_are_disabled(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=False)
    _write_pending_update(repository, "999.0.0")

    with patch("vibe.setup.update_prompt.ask_update_prompt") as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)

    mock_ask.assert_not_called()


def test_no_op_when_no_pending_update_is_cached(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)

    with patch("vibe.setup.update_prompt.ask_update_prompt") as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)

    mock_ask.assert_not_called()


def test_prompt_is_shown_and_continue_returns_without_exiting(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ) as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)

    mock_ask.assert_called_once()


def test_quit_exits_zero(repository: FileSystemUpdateCacheRepository) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.QUIT,
        ),
        pytest.raises(SystemExit) as excinfo,
    ):
        _maybe_run_startup_update_prompt(config, repository)

    assert excinfo.value.code == 0


def test_successful_update_exits_zero(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.UPDATED,
        ),
        pytest.raises(SystemExit) as excinfo,
    ):
        _maybe_run_startup_update_prompt(config, repository)

    assert excinfo.value.code == 0


def test_failed_update_exits_one(repository: FileSystemUpdateCacheRepository) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.UPDATE_FAILED,
        ),
        pytest.raises(SystemExit) as excinfo,
    ):
        _maybe_run_startup_update_prompt(config, repository)

    assert excinfo.value.code == 1


def test_no_op_when_cache_read_raises_oserror() -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    repository = _BrokenRepository()

    with patch("vibe.setup.update_prompt.ask_update_prompt") as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)

    mock_ask.assert_not_called()


def test_check_upgrade_fetches_and_prompts_when_update_is_available(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    notifier = FakeUpdateGateway(update=Update(latest_version="999.0.0"))

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ) as mock_ask:
        _run_check_upgrade(repository, update_notifier=notifier, theme="textual-light")

    mock_ask.assert_called_once_with(
        __version__,
        "999.0.0",
        theme="textual-light",
        prompt_mode=UpdatePromptMode.CHECK_UPGRADE,
    )
    assert notifier.fetch_update_calls == 1


def test_check_upgrade_cancel_does_not_dismiss_update(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    notifier = FakeUpdateGateway(update=Update(latest_version="999.0.0"))

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ):
        _run_check_upgrade(repository, update_notifier=notifier)

    cache = asyncio.run(repository.get())
    assert cache is not None
    assert cache.dismissed_version is None

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ) as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)

    mock_ask.assert_called_once()


def test_check_upgrade_prints_up_to_date_when_no_update_exists(
    repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    notifier = FakeUpdateGateway(update=None)

    with patch("vibe.setup.update_prompt.ask_update_prompt") as mock_ask:
        _run_check_upgrade(repository, update_notifier=notifier)

    mock_ask.assert_not_called()
    out = capsys.readouterr().out
    assert "already up to date" in out
    assert __version__ in out


def test_check_upgrade_exits_one_when_gateway_errors(
    repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    notifier = FakeUpdateGateway(
        error=UpdateGatewayError(cause=UpdateGatewayCause.REQUEST_FAILED)
    )

    with pytest.raises(SystemExit) as excinfo:
        _run_check_upgrade(repository, update_notifier=notifier)

    assert excinfo.value.code == 1
    assert "Update check failed" in capsys.readouterr().out


def test_continue_marks_version_as_dismissed_and_prevents_reprompt(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ) as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)
        _maybe_run_startup_update_prompt(config, repository)

    assert mock_ask.call_count == 1


def test_continue_reprompts_when_a_newer_version_appears(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ) as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)
        _write_pending_update(repository, "1000.0.0")
        _maybe_run_startup_update_prompt(config, repository)

    assert mock_ask.call_count == 2


def test_successful_update_prints_restart_hint(
    repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.UPDATED,
        ),
        pytest.raises(SystemExit),
    ):
        _maybe_run_startup_update_prompt(config, repository)

    out = capsys.readouterr().out
    assert "999.0.0" in out
    assert "Run" in out and "vibe" in out


def test_failed_update_prints_error_message(
    repository: FileSystemUpdateCacheRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.UPDATE_FAILED,
        ),
        pytest.raises(SystemExit),
    ):
        _maybe_run_startup_update_prompt(config, repository)

    out = capsys.readouterr().out
    assert "could not update automatically" in out
    assert "package manager" in out


def _run_failed_update(
    repository: FileSystemUpdateCacheRepository,
    monkeypatch: pytest.MonkeyPatch,
    *,
    uv_install: bool,
    answer: str,
    reinstalled: bool = True,
) -> tuple[int | str | None, AsyncMock]:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")
    force_reinstall = AsyncMock(return_value=reinstalled)
    monkeypatch.setattr("vibe.cli.cli.is_uv_tool_install", lambda: uv_install)
    monkeypatch.setattr("vibe.cli.cli.force_reinstall_latest", force_reinstall)
    monkeypatch.setattr("builtins.input", lambda: answer)

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.UPDATE_FAILED,
        ),
        pytest.raises(SystemExit) as exit_info,
    ):
        _maybe_run_startup_update_prompt(config, repository)

    return exit_info.value.code, force_reinstall


def test_failed_uv_update_reinstalls_latest_when_user_answers_yes(
    repository: FileSystemUpdateCacheRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, force_reinstall = _run_failed_update(
        repository, monkeypatch, uv_install=True, answer="y"
    )

    out = capsys.readouterr().out
    assert "uv tool install --force mistral-vibe@latest" in out
    assert "updated from" in out
    assert code == 0
    force_reinstall.assert_awaited_once_with("999.0.0")


def test_failed_uv_update_does_not_reinstall_when_user_answers_no(
    repository: FileSystemUpdateCacheRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, force_reinstall = _run_failed_update(
        repository, monkeypatch, uv_install=True, answer=""
    )

    assert "could not update automatically" in capsys.readouterr().out
    assert code == 1
    force_reinstall.assert_not_awaited()


def test_failed_reinstall_prints_error_message(
    repository: FileSystemUpdateCacheRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _ = _run_failed_update(
        repository, monkeypatch, uv_install=True, answer="y", reinstalled=False
    )

    assert "could not update automatically" in capsys.readouterr().out
    assert code == 1


def test_failed_non_uv_update_does_not_offer_reinstall(
    repository: FileSystemUpdateCacheRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, force_reinstall = _run_failed_update(
        repository, monkeypatch, uv_install=False, answer="y"
    )

    assert "--force" not in capsys.readouterr().out
    assert code == 1
    force_reinstall.assert_not_awaited()


def test_failed_update_does_not_dismiss_so_user_is_reprompted_on_next_launch(
    repository: FileSystemUpdateCacheRepository,
) -> None:
    config = build_test_vibe_config(enable_update_checks=True)
    _write_pending_update(repository, "999.0.0")

    with (
        patch(
            "vibe.setup.update_prompt.ask_update_prompt",
            return_value=UpdatePromptResult.UPDATE_FAILED,
        ),
        pytest.raises(SystemExit),
    ):
        _maybe_run_startup_update_prompt(config, repository)

    cache = asyncio.run(repository.get())
    assert cache is not None
    assert cache.dismissed_version is None

    with patch(
        "vibe.setup.update_prompt.ask_update_prompt",
        return_value=UpdatePromptResult.CONTINUE,
    ) as mock_ask:
        _maybe_run_startup_update_prompt(config, repository)

    mock_ask.assert_called_once()
