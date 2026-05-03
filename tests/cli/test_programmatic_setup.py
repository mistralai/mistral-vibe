from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from vibe.cli import cli as cli_mod, entrypoint as entrypoint_mod
from vibe.core.config import MissingAPIKeyError
from vibe.core.trusted_folders import trusted_folders_manager


def _make_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "initial_prompt": None,
        "prompt": "hello",
        "max_turns": None,
        "max_price": None,
        "enabled_tools": None,
        "output": "text",
        "agent": "default",
        "setup": False,
        "workdir": None,
        "trust": False,
        "teleport": False,
        "continue_session": False,
        "resume": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_programmatic_mode_does_not_run_onboarding_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise MissingAPIKeyError("MISTRAL_API_KEY", "mistral")

    monkeypatch.setattr(cli_mod.VibeConfig, "load", staticmethod(boom))

    sentinel: dict[str, bool] = {"called": False}

    def fail_onboarding(*_args: object, **_kwargs: object) -> None:
        sentinel["called"] = True

    monkeypatch.setattr(cli_mod, "run_onboarding", fail_onboarding)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.load_config_or_exit(interactive=False)

    assert exc_info.value.code == 1
    assert sentinel["called"] is False
    err = capsys.readouterr().err
    assert "MISTRAL_API_KEY" in err
    assert "vibe --setup" in err


def test_interactive_mode_still_runs_onboarding_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replace VibeConfig.load with a stub that fails the first time.
    state = {"raised": False}

    def fake_load() -> object:
        if not state["raised"]:
            state["raised"] = True
            raise MissingAPIKeyError("MISTRAL_API_KEY", "mistral")
        return "config-sentinel"

    monkeypatch.setattr(cli_mod.VibeConfig, "load", staticmethod(fake_load))

    onboarding_called: list[bool] = []
    monkeypatch.setattr(
        cli_mod, "run_onboarding", lambda *a, **k: onboarding_called.append(True)
    )

    result = cli_mod.load_config_or_exit(interactive=True)
    assert onboarding_called == [True]
    assert result == "config-sentinel"


def test_warn_if_workdir_untrusted_writes_stderr_when_project_config_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("hello", encoding="utf-8")
    monkeypatch.chdir(project)

    cli_mod.warn_if_workdir_trust_is_unset()

    err = " ".join(capsys.readouterr().err.split())
    assert "not trusted" in err
    assert "AGENTS.md" in err
    assert "--trust" in err


def test_warn_if_workdir_untrusted_silent_when_already_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("hello", encoding="utf-8")
    monkeypatch.chdir(project)

    trusted_folders_manager.add_trusted(project)

    cli_mod.warn_if_workdir_trust_is_unset()

    assert capsys.readouterr().err == ""


def test_warn_if_workdir_untrusted_silent_when_no_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    cli_mod.warn_if_workdir_trust_is_unset()

    assert capsys.readouterr().err == ""


def test_trust_flag_trusts_cwd_for_session_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    args = _make_args(trust=True, prompt=None)
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        entrypoint_mod, "check_and_resolve_trusted_folder", lambda _cwd: None
    )
    monkeypatch.setattr(
        entrypoint_mod, "init_harness_files_manager", lambda *a, **k: None
    )
    # Stop main() before it runs the actual CLI.
    monkeypatch.setattr(
        "vibe.cli.cli.run_cli", lambda _args: (_ for _ in ()).throw(SystemExit(0))
    )

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()
    assert exc_info.value.code == 0

    assert trusted_folders_manager.is_trusted(project) is True
    # --trust must NOT persist to trusted_folders.toml.
    assert trusted_folders_manager._trusted == []
    assert str(project.resolve()) in trusted_folders_manager._session_trusted


def test_trust_flag_works_in_programmatic_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    args = _make_args(trust=True, prompt="run")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        entrypoint_mod,
        "check_and_resolve_trusted_folder",
        lambda _cwd: pytest.fail("must not prompt in -p mode"),
    )
    monkeypatch.setattr(
        entrypoint_mod, "init_harness_files_manager", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "vibe.cli.cli.run_cli", lambda _args: (_ for _ in ()).throw(SystemExit(0))
    )

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    assert trusted_folders_manager.is_trusted(project) is True
    assert trusted_folders_manager._trusted == []


def test_session_trust_does_not_write_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_file = tmp_path / "trusted_folders.toml"
    monkeypatch.setattr(trusted_folders_manager, "_file_path", trust_file)
    project = tmp_path / "proj"
    project.mkdir()

    trusted_folders_manager.trust_for_session(project)

    assert trusted_folders_manager.is_trusted(project) is True
    assert not trust_file.exists()
