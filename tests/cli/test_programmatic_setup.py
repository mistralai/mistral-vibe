from __future__ import annotations

import argparse
from pathlib import Path

from git import Repo
import pytest

from tests.conftest import OrchestratorLoader, build_test_vibe_config
from vibe.app_server.local import LocalHarnessOptions
from vibe.cli import (
    cli as cli_mod,
    entrypoint as entrypoint_mod,
    programmatic as programmatic_mod,
)
from vibe.core.config import MissingAPIKeyError, VibeConfigSchema, harness_files
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.git.worktree import ManagedWorktree, WorktreeRepository
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.setup import onboarding as onboarding_mod, update_prompt as update_prompt_mod


def _prepare(name: str, base: Path) -> None:
    with WorktreeRepository.open(base) as repository:
        repository.prepare(name)


def _holders(cwd: Path) -> frozenset[str]:
    managed = ManagedWorktree.at(cwd)
    return frozenset() if managed is None else managed.holders()


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
        "agent": "ask",
        "experimental_harness": False,
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
    async def boom() -> ConfigOrchestrator[VibeConfigSchema]:
        raise MissingAPIKeyError("MISTRAL_API_KEY", "mistral")

    monkeypatch.setattr(cli_mod, "build_default_orchestrator", boom)

    sentinel: dict[str, bool] = {"called": False}

    def fail_onboarding(*_args: object, **_kwargs: object) -> None:
        sentinel["called"] = True

    monkeypatch.setattr(onboarding_mod, "run_onboarding", fail_onboarding)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.load_config_orchestrator_or_exit(interactive=False)

    assert exc_info.value.code == 1
    assert sentinel["called"] is False
    err = capsys.readouterr().err
    assert "MISTRAL_API_KEY" in err
    assert "vibe --setup" in err


def test_interactive_mode_still_runs_onboarding_on_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    # The initial config load fails; onboarding then builds and returns the
    # orchestrator itself (persisting the chosen theme through it).
    sentinel_config = build_test_vibe_config(displayed_workdir="/sentinel/workdir")

    async def fake_load() -> ConfigOrchestrator[VibeConfigSchema]:
        raise MissingAPIKeyError("MISTRAL_API_KEY", "mistral")

    monkeypatch.setattr(cli_mod, "build_default_orchestrator", fake_load)

    onboarding_called: list[bool] = []

    def fake_onboarding(
        *a: object, **k: object
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        onboarding_called.append(True)
        return load_orchestrator(sentinel_config)

    monkeypatch.setattr(onboarding_mod, "run_onboarding", fake_onboarding)

    result = cli_mod.load_config_orchestrator_or_exit(interactive=True)
    assert onboarding_called == [True]
    assert result.config.displayed_workdir == "/sentinel/workdir"


def test_interactive_trust_flag_is_delegated_without_launcher_mutation(
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
        assert _args.trust is True
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()
    assert exc_info.value.code == 0

    assert trusted_folders_manager.is_trusted(project) is None
    assert trusted_folders_manager._trusted == []
    assert trusted_folders_manager._session_trusted == []


def test_programmatic_trust_flag_is_delegated_without_launcher_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    args = _make_args(trust=True, prompt="run")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        assert _args.trust is True
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    assert trusted_folders_manager.is_trusted(project) is None
    assert trusted_folders_manager._trusted == []


def test_check_upgrade_does_not_start_interactive_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "AGENTS.md").write_text("hello", encoding="utf-8")
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, check_upgrade=True)
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(_args: argparse.Namespace) -> None:
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


@pytest.mark.parametrize(
    ("prompt", "exit_code"),
    [
        # -p never reaches the cleanup gate, and neither does a failed start.
        ("do the thing", 0),
        (None, 1),
    ],
)
def test_worktree_holder_is_released_even_when_cleanup_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str | None, exit_code: int
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt=prompt, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    worktree_path: list[Path] = []

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        worktree_path.append(Path.cwd())
        raise SystemExit(exit_code)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    # A marker left here reads as a live session forever: every later release
    # reports the worktree in use, and the sweep only reclaims reservations
    # that never became one.
    assert _holders(worktree_path[0]) == frozenset()


def test_worktree_cleanup_stays_held_while_the_prompt_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _init_repo(project)
    monkeypatch.chdir(project)

    args = _make_args(prompt=None, worktree="feature")
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )
    worktree_path: list[Path] = []
    held_at_prompt: list[frozenset[str]] = []

    def answer_prompt() -> str:
        # An app-server sweeping this repo reads the markers. Releasing before
        # the prompt would let it remove the worktree while the user decides.
        held_at_prompt.append(_holders(worktree_path[0]))
        return ""

    monkeypatch.setattr("builtins.input", answer_prompt)

    def fake_run_cli(_args: argparse.Namespace, **_kwargs: object) -> None:
        path = Path.cwd()
        worktree_path.append(path)
        (path / "new.txt").write_text("keep me\n", encoding="utf-8")
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit):
        entrypoint_mod.main()

    assert held_at_prompt, "the dirty worktree should have prompted"
    assert held_at_prompt[0], "the CLI must still hold the worktree at the prompt"
    assert _holders(worktree_path[0]) == frozenset()


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
    _prepare("feature", project)

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


def test_interactive_start_delegates_trust_to_textual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)

    args = _make_args(prompt=None)
    monkeypatch.setattr(entrypoint_mod, "parse_arguments", lambda: args)
    monkeypatch.setattr(
        harness_files, "init_harness_files_manager", lambda *a, **k: None
    )

    def fake_run_cli(_args: argparse.Namespace) -> None:
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli.cli.run_cli", fake_run_cli)

    with pytest.raises(SystemExit) as exc_info:
        entrypoint_mod.main()

    assert exc_info.value.code == 0


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
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(max_tokens=123)
    call: dict[str, object] = {}
    config = build_test_vibe_config()

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "load_config_orchestrator_or_exit",
        lambda interactive: load_orchestrator(config),
    )
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)

    def fake_run_programmatic(**kwargs: object) -> str:
        call.update(kwargs)
        return "done"

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.session_options.max_session_tokens == 123


def test_run_cli_auto_approve_is_a_harness_option_without_changing_agent(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(agent="lean", auto_approve=True, trust=True)
    call: dict[str, object] = {}
    config = build_test_vibe_config(default_agent="plan")
    orchestrator = load_orchestrator(config)

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod, "load_config_orchestrator_or_exit", lambda interactive: orchestrator
    )
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)

    def fake_run_programmatic(**kwargs: object) -> str:
        call.update(kwargs)
        return "done"

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.session_options.agent == "lean"
    assert options.session_options.auto_approve is True
    assert options.session_options.trust_workspace is True
    assert config.bypass_tool_permissions is False


def test_run_cli_forwards_experimental_harness_selection(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(experimental_harness=True)
    call: dict[str, object] = {}
    config = build_test_vibe_config()
    orchestrator = load_orchestrator(config)

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod, "load_config_orchestrator_or_exit", lambda interactive: orchestrator
    )
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)

    def fake_run_programmatic(**kwargs: object) -> str:
        call.update(kwargs)
        return "done"

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.experimental_harness is True


def _patch_run_cli_for_config(
    monkeypatch: pytest.MonkeyPatch,
    config: VibeConfigSchema,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> dict[str, object]:
    call: dict[str, object] = {}
    orchestrator = load_orchestrator(config)
    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod, "load_config_orchestrator_or_exit", lambda *, interactive: orchestrator
    )
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)

    def fake_run_programmatic(**kwargs: object) -> str:
        call.update(kwargs)
        return "done"

    monkeypatch.setattr(programmatic_mod, "run_programmatic", fake_run_programmatic)
    return call


def test_run_cli_disabled_tools_filter_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(enabled_tools=["bash"], disabled_tools=["bash"])
    config = build_test_vibe_config()
    call = _patch_run_cli_for_config(monkeypatch, config, load_orchestrator)

    with pytest.raises(SystemExit):
        cli_mod.run_cli(args)

    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.session_options.enabled_tools == ["bash"]
    assert "bash" in options.session_options.disabled_tools


def test_run_cli_programmatic_disabled_tools_filter_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(enabled_tools=["ask_user_question", "exit_plan_mode", "grep"])
    config = build_test_vibe_config()
    call = _patch_run_cli_for_config(monkeypatch, config, load_orchestrator)

    with pytest.raises(SystemExit):
        cli_mod.run_cli(args)

    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.session_options.enabled_tools == [
        "ask_user_question",
        "exit_plan_mode",
        "grep",
    ]
    assert "ask_user_question" in options.session_options.disabled_tools
    assert "exit_plan_mode" in options.session_options.disabled_tools


def test_run_cli_disabled_tools_concatenated_when_no_enabled_tools(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(disabled_tools=["bash"])
    config = build_test_vibe_config(disabled_tools=["webfetch"])
    call = _patch_run_cli_for_config(monkeypatch, config, load_orchestrator)

    with pytest.raises(SystemExit):
        cli_mod.run_cli(args)

    options = call["harness_options"]
    assert isinstance(options, LocalHarnessOptions)
    assert options.session_options.enabled_tools is None
    assert "bash" in options.session_options.disabled_tools


def test_run_cli_runs_update_prompt_before_interactive_start(
    monkeypatch: pytest.MonkeyPatch,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    args = _make_args(prompt=None)
    config = build_test_vibe_config()
    calls: list[str] = []

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "load_config_orchestrator_or_exit",
        lambda interactive: load_orchestrator(config),
    )
    monkeypatch.setattr(cli_mod, "get_prompt_from_stdin", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "_maybe_run_startup_update_prompt",
        lambda _config, _repository: calls.append("update"),
    )

    def run_interactive(**_kwargs: object) -> None:
        calls.append("interactive")
        raise SystemExit(0)

    monkeypatch.setattr(cli_mod, "_run_interactive_mode", run_interactive)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    assert calls == ["update", "interactive"]


def test_run_cli_check_upgrade_exits_before_loading_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _make_args(prompt=None, check_upgrade=True)
    call: dict[str, object] = {}

    monkeypatch.setattr(cli_mod, "bootstrap_config_files", lambda: None)
    monkeypatch.setattr(
        cli_mod,
        "load_config_orchestrator_or_exit",
        lambda interactive: pytest.fail("check-upgrade should not load config"),
    )
    monkeypatch.setattr(
        update_prompt_mod, "load_update_prompt_theme", lambda: "dracula"
    )

    def fake_run_check_upgrade(_repository: object, *, theme: str | None) -> None:
        call["theme"] = theme

    monkeypatch.setattr(cli_mod, "_run_check_upgrade", fake_run_check_upgrade)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.run_cli(args)

    assert exc_info.value.code == 0
    assert call["theme"] == "dracula"
