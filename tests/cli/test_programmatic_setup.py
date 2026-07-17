from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from git import Repo
import pytest

from tests.conftest import build_test_vibe_config
from vibe.cli import cli as cli_mod, entrypoint as entrypoint_mod
from vibe.core import programmatic as programmatic_mod
from vibe.core.config import MissingAPIKeyError, VibeConfig, harness_files
from vibe.core.tools.manager import ToolManager
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.worktree import prepare_worktree_session
from vibe.setup import onboarding as onboarding_mod, update_prompt as update_prompt_mod


def _make_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "initial_prompt": None,
        "prompt": "hello",
        "max_turns": None,
        "max_price": None,
        "max_tokens": None,
        "enabled_tools": None,
        "disabled_tools": None,
        "output": "text",
        "agent": "default",
        "auto_approve": False,
        "check_upgrade": False,
        "setup": False,
        "workdir": None,
        "worktree": None,
        "add_dir": [],
        "trust": False,
        "teleport": False,
        "continue_session": False,
        "resume": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _init_repo(workdir: Path) -> Repo:
    repo = Repo.init(workdir, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (workdir / "file.txt").write_text("hello\n", encoding="utf-8")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def test_programmatic_mode_does_not_run_onboarding_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise MissingAPIKeyError("MISTRAL_API_KEY", "mistral")

    monkeypatch.setattr(cli_mod.VibeConfig, "load", staticmethod(boom))

    sentinel: dict[str, bool] = {"called": False}

    def fail_onboarding(*_args: object, **_kwargs: object) -> None:
        sentinel["called"] = True

    monkeypatch.setattr(onboarding_mod, "run_onboarding", fail_onboarding)

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
        onboarding_mod, "run_onboarding", lambda *a, **k: onboarding_called.append(True)
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
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    # Stop main() before it runs the actual CLI.
    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

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
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    assert trusted_folders_manager.is_trusted(project) is True
    assert trusted_folders_manager._trusted == []


def test_check_upgrade_does_not_pass_trust_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("hello", encoding="utf-8")
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, check_upgrade=True)
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        entrypoint_mod,
        "check_and_resolve_trusted_folder",
        lambda _cwd: pytest.fail("check-upgrade must not prompt for trust"),
    )
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(
        _args: argparse.Namespace,
        *,
        resolve_trusted_folder: Callable[[], None] | None = None,
    ) -> None:
        assert resolve_trusted_folder is None
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    assert exc_info.value.code == 0


@pytest.mark.parametrize("flag", ["check_upgrade", "setup"])
def test_exit_only_modes_do_not_prepare_worktree(
    flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature", **{flag: True})
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    assert exc_info.value.code == 0
    assert Path.cwd() == project


def test_worktree_start_prints_progress_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Preparing worktree 'feature'..." in captured.err
    assert "Using worktree:" in captured.err
    assert "Removing worktree:" in captured.err
    assert "Removed worktree:" in captured.err
    assert "feature" not in (h.name for h in repo.heads)


def test_worktree_cleanup_prompt_keeps_dirty_worktree_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    monkeypatch.setattr("builtins.input", lambda: "")
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        path = Path.cwd()
        worktree_path.append(path)
        (path / "new.txt").write_text("keep me\n", encoding="utf-8")
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    captured = capsys.readouterr()
    assert "untracked files" in captured.err
    assert "Keeping worktree:" in captured.err
    assert worktree_path[0].exists()
    assert "feature" in (h.name for h in repo.heads)


def test_worktree_cleanup_prompt_removes_dirty_worktree_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    monkeypatch.setattr("builtins.input", lambda: "remove")
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        path = Path.cwd()
        worktree_path.append(path)
        (path / "new.txt").write_text("discard me\n", encoding="utf-8")
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    captured = capsys.readouterr()
    assert "untracked files" in captured.err
    assert "Removing worktree:" in captured.err
    assert "Removed worktree:" in captured.err
    assert not worktree_path[0].exists()
    assert "feature" not in (h.name for h in repo.heads)


def test_programmatic_worktree_is_not_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt="run", worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        worktree_path.append(Path.cwd())
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    assert worktree_path[0].exists()
    assert "feature" in (h.name for h in repo.heads)


def test_worktree_cleanup_skips_failed_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        worktree_path.append(Path.cwd())
        raise SystemExit(1)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    assert exc_info.value.code == 1
    assert worktree_path[0].exists()
    assert "Removing worktree:" not in capsys.readouterr().err
    assert "feature" in (h.name for h in repo.heads)


def test_reused_worktree_is_not_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    monkeypatch.chdir(project)
    prepare_worktree_session("feature", project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        worktree_path.append(Path.cwd())
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    assert worktree_path[0].exists()
    assert "Removing worktree:" not in capsys.readouterr().err
    assert "feature" in (h.name for h in repo.heads)


def test_attached_branch_is_kept_on_cleanup_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    repo = _init_repo(project)
    repo.create_head("feature")
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    monkeypatch.setattr("builtins.input", lambda: "")
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        worktree_path.append(Path.cwd())
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    captured = capsys.readouterr()
    assert "Removed worktree:" in captured.err
    assert "Kept branch: feature" in captured.err
    assert not worktree_path[0].exists()
    assert "feature" in (h.name for h in repo.heads)


def test_interactive_start_passes_trust_resolver_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    args = _make_args(prompt=None)
    calls: list[str] = []
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        entrypoint_mod,
        "check_and_resolve_trusted_folder",
        lambda _cwd: calls.append("trust"),
    )
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(
        _args: argparse.Namespace,
        *,
        resolve_trusted_folder: Callable[[], None] | None = None,
    ) -> None:
        assert callable(resolve_trusted_folder)
        resolve_trusted_folder()
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    assert exc_info.value.code == 0
    assert calls == ["trust"]


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


def test_run_cli_passes_max_tokens_to_run_programmatic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(max_tokens=123)
    call: dict[str, object] = {}
    config = build_test_vibe_config()

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(cli_mod, "load_config_or_exit", lambda interactive: config)
    monkeypatch.setattr(cli_mod, "load_hooks_from_fs", lambda: None)
    monkeypatch.setattr(cli_mod, "setup_tracing", lambda _config: None)
    monkeypatch.setattr(cli_mod, "load_session", lambda _args, _config: None)
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)
    monkeypatch.setattr(cli_mod, "warn_if_workdir_trust_is_unset", lambda: None)
    monkeypatch.setattr(cli_mod, "get_initial_agent_name", lambda _args, _config: "x")

    def fake_run_programmatic(**kwargs: object) -> str:
        call.update(kwargs)
        return "done"

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    assert call["max_session_tokens"] == 123


def test_run_cli_auto_approve_sets_config_without_changing_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(agent="lean", auto_approve=True)
    call: dict[str, object] = {}
    config = build_test_vibe_config(default_agent="plan")

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(cli_mod, "load_config_or_exit", lambda interactive: config)
    monkeypatch.setattr(cli_mod, "load_hooks_from_fs", lambda: None)
    monkeypatch.setattr(cli_mod, "setup_tracing", lambda _config: None)
    monkeypatch.setattr(cli_mod, "load_session", lambda _args, _config: None)
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)
    monkeypatch.setattr(cli_mod, "warn_if_workdir_trust_is_unset", lambda: None)

    def fake_run_programmatic(**kwargs: object) -> str:
        call.update(kwargs)
        return "done"

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    assert call["agent_name"] == "lean"
    assert config.bypass_tool_permissions is True


def _patch_run_cli_for_config(
    monkeypatch: pytest.MonkeyPatch, config: VibeConfig
) -> None:
    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(cli_mod, "load_config_or_exit", lambda interactive: config)
    monkeypatch.setattr(cli_mod, "load_hooks_from_fs", lambda: None)
    monkeypatch.setattr(cli_mod, "setup_tracing", lambda _config: None)
    monkeypatch.setattr(cli_mod, "load_session", lambda _args, _config: None)
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)
    monkeypatch.setattr(cli_mod, "warn_if_workdir_trust_is_unset", lambda: None)
    monkeypatch.setattr(cli_mod, "get_initial_agent_name", lambda _args, _config: "x")
    monkeypatch.setattr(programmatic_mod, "run_programmatic", lambda **kwargs: "done")


def test_run_cli_disabled_tools_filter_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(enabled_tools=["bash"], disabled_tools=["bash"])
    config = build_test_vibe_config()
    _patch_run_cli_for_config(monkeypatch, config)

    with pytest.raises(SystemExit):
        cli_mod.run_cli(args)

    assert config.enabled_tools == ["bash"]
    assert "bash" in config.disabled_tools
    assert ToolManager(lambda: config).available_tools == {}


def test_run_cli_programmatic_disabled_tools_filter_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(enabled_tools=["ask_user_question", "exit_plan_mode", "grep"])
    config = build_test_vibe_config()
    _patch_run_cli_for_config(monkeypatch, config)

    with pytest.raises(SystemExit):
        cli_mod.run_cli(args)

    available_tools = ToolManager(lambda: config).available_tools
    assert "ask_user_question" not in available_tools
    assert "exit_plan_mode" not in available_tools
    assert "grep" in available_tools


def test_run_cli_disabled_tools_concatenated_when_no_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(disabled_tools=["bash"])
    config = build_test_vibe_config(disabled_tools=["webfetch"])
    _patch_run_cli_for_config(monkeypatch, config)

    with pytest.raises(SystemExit):
        cli_mod.run_cli(args)

    assert config.enabled_tools == []
    assert "webfetch" in config.disabled_tools
    assert "bash" in config.disabled_tools


def test_run_cli_runs_update_prompt_before_trust_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(prompt=None)
    config = build_test_vibe_config()
    calls: list[str] = []

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(cli_mod, "load_config_or_exit", lambda interactive: config)
    monkeypatch.setattr(
        cli_mod,
        "_maybe_run_startup_update_prompt",
        lambda _config, _repository: calls.append("update"),
    )

    def resolve_trusted_folder() -> None:
        calls.append("trust")
        raise SystemExit(0)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args, resolve_trusted_folder=resolve_trusted_folder)

    assert exc_info.value.code == 0
    assert calls == ["update", "trust"]


def test_run_cli_check_upgrade_exits_before_loading_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(prompt=None, check_upgrade=True)
    call: dict[str, object] = {}

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "load_config_or_exit",
        lambda interactive: pytest.fail("check-upgrade should not load config"),
    )
    monkeypatch.setattr(
        update_prompt_mod, "load_update_prompt_theme", lambda: "dracula"
    )

    def fake_run_check_upgrade(_repository: object, *, theme: str | None) -> None:
        call["theme"] = theme

    monkeypatch.setattr(cli_mod, "_run_check_upgrade", fake_run_check_upgrade)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(
            args,
            resolve_trusted_folder=lambda: pytest.fail(
                "check-upgrade should not prompt for trust"
            ),
        )

    assert exc_info.value.code == 0
    assert call["theme"] == "dracula"
