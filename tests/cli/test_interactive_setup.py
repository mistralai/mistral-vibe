from __future__ import annotations

import argparse
from typing import Any

import pytest

from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from vibe.app_server import local as local_harness_mod
from vibe.cli import cli as cli_mod
from vibe.cli.textual_ui import app as textual_app_mod


def _make_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "initial_prompt": None,
        "prompt": None,
        "agent": None,
        "experimental_harness": False,
        "auto_approve": False,
        "enabled_tools": None,
        "disabled_tools": None,
        "add_dir": [],
        "trust": False,
        "worktree": None,
        "teleport": False,
        "continue_session": False,
        "resume": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def captured_startup(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Records what the interactive launch hands the TUI, without running it."""
    call: dict[str, Any] = {}

    def fake_run_textual_ui(**kwargs: Any) -> None:
        call.update(kwargs)
        return None

    real_harness = local_harness_mod.LocalHarness

    def recording_harness(options: Any) -> Any:
        call["harness_options"] = options
        return real_harness(options)

    monkeypatch.setattr(textual_app_mod, "run_textual_ui", fake_run_textual_ui)
    monkeypatch.setattr(local_harness_mod, "LocalHarness", recording_harness)
    return call


def _run(args: argparse.Namespace) -> None:
    cli_mod._run_interactive_mode(
        args=args,
        stdin_prompt=None,
        update_cache_repository=FakeUpdateCacheRepository(),
    )


def test_trust_prompt_is_shown_by_default(captured_startup: dict[str, Any]) -> None:
    _run(_make_args())

    assert captured_startup["startup"].prompt_for_workspace_trust is True


def test_worktree_skips_the_trust_prompt(captured_startup: dict[str, Any]) -> None:
    # A worktree is a fresh directory every session, so prompting would ask
    # again on every launch for trust the session grants itself anyway.
    _run(_make_args(worktree=True))

    assert captured_startup["startup"].prompt_for_workspace_trust is False
    options = captured_startup["harness_options"]
    assert options.session_options.trust_workspace is True


def test_trust_flag_skips_the_trust_prompt(captured_startup: dict[str, Any]) -> None:
    _run(_make_args(trust=True))

    assert captured_startup["startup"].prompt_for_workspace_trust is False
