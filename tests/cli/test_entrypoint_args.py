from __future__ import annotations

from types import SimpleNamespace

import pytest

from vibe import _experimental_harness
import vibe.cli.entrypoint as entrypoint
from vibe.cli.entrypoint import parse_arguments
from vibe.core.config.harness_files import (
    get_harness_files_manager,
    reset_harness_files_manager,
)


def _parse(monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    monkeypatch.setattr("sys.argv", ["vibe", *argv])
    return parse_arguments()


def test_disabled_tools_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse(monkeypatch, [])
    assert args.disabled_tools is None


def test_disabled_tools_appends_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse(monkeypatch, ["--disabled-tools", "bash", "--disabled-tools", "web*"])
    assert args.disabled_tools == ["bash", "web*"]


def test_enabled_and_disabled_tools_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse(monkeypatch, ["--enabled-tools", "read", "--disabled-tools", "bash"])
    assert args.enabled_tools == ["read"]
    assert args.disabled_tools == ["bash"]


def test_experimental_harness_flag_is_parseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse(monkeypatch, ["--experimental-harness"])

    assert args.experimental_harness is True


def test_experimental_harness_is_hidden_without_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        _experimental_harness, "experimental_harness_available", lambda: False
    )

    with pytest.raises(SystemExit) as exc_info:
        _parse(monkeypatch, ["--help"])

    assert exc_info.value.code == 0
    assert "--experimental-harness" not in capsys.readouterr().out


def test_experimental_harness_is_visible_with_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        _experimental_harness, "experimental_harness_available", lambda: True
    )

    with pytest.raises(SystemExit) as exc_info:
        _parse(monkeypatch, ["--help"])

    assert exc_info.value.code == 0
    assert "--experimental-harness" in capsys.readouterr().out


def test_experimental_harness_factory_comes_from_harness_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported_modules: list[str] = []

    def create_host_stub():
        raise NotImplementedError("Harness stub selected")

    def import_stub(module_name: str):
        imported_modules.append(module_name)
        return SimpleNamespace(create_harness_host=create_host_stub)

    monkeypatch.setattr(_experimental_harness, "import_module", import_stub)

    with pytest.raises(NotImplementedError, match="Harness stub selected"):
        _experimental_harness.create_experimental_harness_host()

    assert imported_modules == ["mistralai_rust_harness.vibe"]


def test_worktree_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _parse(monkeypatch, []).worktree is None


def test_bare_worktree_requests_auto_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _parse(monkeypatch, ["--worktree"]).worktree is True


def test_worktree_with_a_value_keeps_the_name(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _parse(monkeypatch, ["--worktree", "feat-x"]).worktree == "feat-x"


def test_worktree_after_the_prompt_auto_names(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse(monkeypatch, ["Fix the login bug", "--worktree"])

    assert args.worktree is True
    assert args.initial_prompt == "Fix the login bug"


# Documents an argparse trap shared with --resume: an optional-value flag eats
# the next token, so the prompt must precede --worktree or follow a "--".
def test_worktree_before_the_prompt_consumes_it_as_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse(monkeypatch, ["--worktree", "Fix the login bug"])

    assert args.worktree == "Fix the login bug"
    assert args.initial_prompt is None


def test_double_dash_separates_the_prompt_from_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse(monkeypatch, ["--worktree", "--", "Fix the login bug"])

    assert args.worktree is True
    assert args.initial_prompt == "Fix the login bug"


def test_suggest_worktree_name_reads_the_dotenv_before_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def suggest(prompt: str | None, **_kwargs: object) -> str:
        calls.append(f"suggest:{prompt}")
        return "repair-oauth-redirect"

    monkeypatch.setattr(
        "vibe.core.config.vibe_schema.load_dotenv_values",
        lambda *_args, **_kwargs: calls.append("dotenv"),
    )
    monkeypatch.setattr(
        "vibe.core.git.worktree.naming_model.suggest_worktree_name", suggest
    )
    # The autouse fixture leaves the singleton initialised, which is not the
    # state a real CLI start is in here. Without this reset the entrypoint's own
    # init is a no-op and dropping it would go unnoticed.
    reset_harness_files_manager()

    assert entrypoint._suggest_worktree_name("Fix the login bug") == (
        "repair-oauth-redirect"
    )
    # The worktree is prepared before run_cli loads ~/.vibe/.env, so a key that
    # lives only there has to be in os.environ before the provider is checked.
    assert calls == ["dotenv", "suggest:Fix the login bug"]
    # Raises unless the entrypoint initialised it. Resolving config prompts goes
    # through this singleton, and main() does not set it up until much later.
    get_harness_files_manager()


def test_suggest_worktree_name_skips_the_model_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no prompt means nothing to name from")

    monkeypatch.setattr("vibe.core.config.vibe_schema.load_dotenv_values", explode)

    assert entrypoint._suggest_worktree_name(None) is None
