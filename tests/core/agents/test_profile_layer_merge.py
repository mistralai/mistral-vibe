from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import pytest
import tomli_w

from tests.conftest import ConfigBuilder, OrchestratorLoader, get_base_config
from vibe.app_server import _runtime as runtime_module
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.registry import apply_profile_overrides
from vibe.core.config import VibeConfigSchema, build_default_orchestrator
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.layers.agent_profile import refuse_profile_shell_timeouts
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.trusted_folders import trusted_folders_manager

# Pins the exact effect each builtin profile has on the effective config once it
# is applied as the top ConfigOrchestrator layer. The layer stack merges each
# field by its declared strategy, so list fields (providers, models,
# disabled_tools, allowlists) union rather than replace.


def _delta(before: Any, after: Any, path: tuple[str, ...] = ()) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            out.update(
                _delta(
                    before.get(key, "__MISSING__"),
                    after.get(key, "__MISSING__"),
                    (*path, key),
                )
            )
        return out
    if before != after:
        out["/".join(path)] = [before, after]
    return out


@pytest.fixture
def orchestrator(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> ConfigOrchestrator[VibeConfigSchema]:
    return load_orchestrator(build_config(installed_agents=["lean"]))


@pytest.fixture
def base(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> dict[str, Any]:
    return orchestrator.config.model_dump(mode="json")


def _merged_delta(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any], name: str
) -> dict[str, list[Any]]:
    manager = AgentManager(orchestrator, initial_agent=name, allow_subagent=True)
    return _delta(base, manager.config.model_dump(mode="json"))


def test_ask_profile_leaves_the_config_untouched(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    assert _merged_delta(orchestrator, base, "ask") == {}


def test_accept_edits_profile_auto_approves_file_edits(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    assert _merged_delta(orchestrator, base, "accept-edits") == {
        "tools/edit": ["__MISSING__", {"permission": "always"}],
        "tools/write_file": ["__MISSING__", {"permission": "always"}],
    }


def test_auto_approve_profile_bypasses_permissions(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    assert _merged_delta(orchestrator, base, "auto-approve") == {
        "bypass_tool_permissions": [False, True]
    }


def test_explore_profile_restricts_tools_and_prompt(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    delta = _merged_delta(orchestrator, base, "explore")
    assert delta["enabled_tools"] == [[], ["grep", "read_file", "skill"]]
    assert delta["system_prompt_id"][1] == "explore"
    assert set(delta) == {"enabled_tools", "system_prompt_id"}


@pytest.mark.parametrize(
    "field", ["console_base_url", "vibe_base_url", "vibe_code_sessions_base_url"]
)
def test_profile_override_cannot_redirect_credential_base_urls(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], field: str
) -> None:
    original = getattr(orchestrator.config, field)

    # A profile from an untrusted checkout tries to redirect a URL that a
    # credential is sent to; the protected field is dropped, not merged.
    apply_profile_overrides(orchestrator, {field: "https://attacker.example"})

    assert getattr(orchestrator.config, field) == original


def test_lean_profile_unions_providers_and_models(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    base_providers = {p["name"] for p in base["providers"]}
    base_models = set(base["models"])

    manager = AgentManager(orchestrator, initial_agent="lean", allow_subagent=True)
    merged = manager.config

    # Applied as a layer: base providers/models are unioned in, not replaced.
    assert {p.name for p in merged.providers} == base_providers | {"mistral-testing"}
    assert set(merged.models) == base_models | {"leanstral"}
    assert merged.active_model == "leanstral"
    assert merged.compaction_model is not None
    assert merged.compaction_model.alias == "devstral-compact"
    assert merged.system_prompt_id == "lean"


def _write_user_config(config_dir: Path, extra: dict[str, Any]) -> None:
    data = get_base_config()
    data.update(extra)
    with (config_dir / "config.toml").open("wb") as f:
        tomli_w.dump(data, f)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unix", "git_bash", "powershell"])
@pytest.mark.parametrize(
    "own", [{}, {"default_timeout": 1200, "max_timeout_seconds": "1200"}]
)
async def test_lean_runs_its_shell_with_its_1200_second_timeouts(
    config_dir: Path, mode: runtime_module._CommandEnvironmentMode, own: dict[str, Any]
) -> None:
    """*Prepare*: A user config with lean installed and no timeouts for the
    command environment's shell tool, or lean's own.
    *Do*: Start the lean agent, and work out Core's bash timeouts as the
    session does in that environment.
    *Assert*: Bash runs with 1200 s by default and at most 1200 s.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import RustBashTimeoutSettings

    shell = runtime_module._command_tool_name(mode)
    _write_user_config(
        config_dir,
        {"installed_agents": ["lean"], **({"tools": {shell: own}} if own else {})},
    )
    orchestrator = await build_default_orchestrator()

    # Do
    manager = AgentManager(orchestrator, initial_agent="lean")
    settings = manager.config.tools.get(shell)
    bash_timeout = runtime_module._bash_timeout(shell, settings)

    # Assert
    assert bash_timeout == RustBashTimeoutSettings(
        default_seconds=1200, max_seconds=1200
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unix", "git_bash", "powershell"])
async def test_lean_refuses_to_replace_the_users_shell_timeouts(
    config_dir: Path, mode: runtime_module._CommandEnvironmentMode
) -> None:
    """*Prepare*: A user config that sets its own timeouts, 120 s and 1800 s, for
    the command environment's shell tool, with lean installed.
    *Do*: Start the lean agent; then, from another agent, switch to it.
    *Assert*: Both are refused with an error naming lean's timeouts and the
    user's; the switch leaves the agent and its 1800 s maximum as they were.
    """
    # Prepare
    shell = runtime_module._command_tool_name(mode)
    _write_user_config(
        config_dir,
        {
            "installed_agents": ["lean"],
            "tools": {shell: {"default_timeout": 120, "max_timeout_seconds": 1800}},
        },
    )
    orchestrator = await build_default_orchestrator()
    expected = (
        f"The 'lean' agent runs tools.{shell} with default_timeout 1200 and"
        " max_timeout_seconds 1200, but your config.toml sets default_timeout 120"
        " and max_timeout_seconds 1800."
    )

    # Do / Assert
    with pytest.raises(ValueError, match=re.escape(expected)):
        AgentManager(orchestrator, initial_agent="lean")

    manager = AgentManager(orchestrator, initial_agent="ask")
    with pytest.raises(ValueError, match=re.escape(expected)):
        manager.switch_profile("lean")
    assert manager.active_profile.name == "ask"
    assert manager.config.tools[shell]["max_timeout_seconds"] == 1800


@pytest.mark.asyncio
async def test_lean_refuses_a_reload_that_sets_the_users_bash_timeouts(
    config_dir: Path,
) -> None:
    """*Prepare*: The lean agent running on a user config with no bash timeouts.
    *Do*: Set a 600 s bash maximum in config.toml, then reload.
    *Assert*: The reload is refused with the configuration error naming both
    maximums, and the running config keeps lean's 1200 s.
    """
    # Prepare
    _write_user_config(config_dir, {"installed_agents": ["lean"]})
    orchestrator = await build_default_orchestrator()
    manager = AgentManager(orchestrator, initial_agent="lean")
    _write_user_config(
        config_dir,
        {"installed_agents": ["lean"], "tools": {"bash": {"max_timeout_seconds": 600}}},
    )

    # Do / Assert
    with pytest.raises(
        InvalidConfigError,
        match=re.escape(
            "The 'lean' agent runs tools.bash with default_timeout 1200 and"
            " max_timeout_seconds 1200, but your config.toml sets"
            " max_timeout_seconds 600."
        ),
    ):
        await orchestrator.reload()
    assert manager.active_profile.name == "lean"
    assert float(manager.config.tools["bash"]["max_timeout_seconds"]) == 1200


@pytest.mark.asyncio
async def test_lean_refuses_a_write_of_the_users_bash_timeouts(
    config_dir: Path,
) -> None:
    """*Prepare*: The lean agent running on a user config with no bash timeouts.
    *Do*: Write a 30 s bash default timeout to the user config.
    *Assert*: The write is refused with the configuration error, config.toml
    is left as it was, and the running config keeps lean's 1200 s.
    """
    # Prepare
    _write_user_config(config_dir, {"installed_agents": ["lean"]})
    orchestrator = await build_default_orchestrator()
    manager = AgentManager(orchestrator, initial_agent="lean")
    on_disk = (config_dir / "config.toml").read_bytes()

    # Do / Assert
    with pytest.raises(
        InvalidConfigError, match=re.escape("your config.toml sets default_timeout 30.")
    ):
        await orchestrator.set_field("/tools/bash/default_timeout", 30)
    assert (config_dir / "config.toml").read_bytes() == on_disk
    assert float(manager.config.tools["bash"]["default_timeout"]) == 1200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "missing"),
    [
        ("default_timeout", "max_timeout_seconds"),
        ("max_timeout_seconds", "default_timeout"),
    ],
)
async def test_lean_refuses_a_write_of_one_bash_timeout_equal_to_its_own(
    config_dir: Path, key: str, missing: str
) -> None:
    """*Prepare*: The lean agent running on a user config with no bash timeouts.
    *Do*: Write one bash timeout, equal to lean's 1200 s, to the user config.
    *Assert*: The write is refused with the error the config would give without
    lean, config.toml is left as it was, and the running config keeps lean's.
    """
    # Prepare
    _write_user_config(config_dir, {"installed_agents": ["lean"]})
    orchestrator = await build_default_orchestrator()
    manager = AgentManager(orchestrator, initial_agent="lean")
    on_disk = (config_dir / "config.toml").read_bytes()

    # Do / Assert
    with pytest.raises(
        InvalidConfigError,
        match=re.escape(
            f"tools.bash: {key} is set without {missing}. Set both, or neither."
        ),
    ):
        await orchestrator.set_field(f"/tools/bash/{key}", 1200)
    assert (config_dir / "config.toml").read_bytes() == on_disk
    assert float(manager.config.tools["bash"]["default_timeout"]) == 1200


def _bash_timeouts(default: object, maximum: object) -> dict[str, Any]:
    return {
        "tools": {"bash": {"default_timeout": default, "max_timeout_seconds": maximum}}
    }


def test_a_profile_cannot_replace_a_user_timeout_one_second_off_at_the_longest() -> (
    None
):
    with pytest.raises(
        InvalidConfigError, match="sets max_timeout_seconds 9007199254740990"
    ):
        refuse_profile_shell_timeouts(
            "lean",
            _bash_timeouts(60, 9007199254740991),
            [("user-toml", _bash_timeouts(60, 9007199254740990))],
        )


def test_timeouts_a_float_would_round_together_are_a_config_error_not_the_same() -> (
    None
):
    # 2**53 + 1 and 2**53 are the same float, but different timeouts, both above
    # the longest.
    with pytest.raises(
        InvalidConfigError,
        match="max_timeout_seconds 9007199254740992 is above 9007199254740991",
    ):
        refuse_profile_shell_timeouts(
            "lean",
            _bash_timeouts(60, 9007199254740993),
            [("user-toml", _bash_timeouts(60, 9007199254740992))],
        )


@pytest.mark.parametrize(
    "theirs",
    [
        pytest.param(1200, id="int"),
        pytest.param("1200", id="quoted"),
        pytest.param(1200.0, id="float"),
        pytest.param("1.2e3", id="quoted float"),
    ],
)
def test_a_user_timeout_equal_to_the_profiles_is_no_clash(theirs: object) -> None:
    refuse_profile_shell_timeouts(
        "lean",
        _bash_timeouts(1200, 1200),
        [("user-toml", _bash_timeouts(theirs, theirs))],
    )


def test_a_user_timeout_above_the_longest_is_a_config_error_not_a_clash() -> None:
    with pytest.raises(
        InvalidConfigError,
        match="max_timeout_seconds 18446744073709551615 is above 9007199254740991",
    ):
        refuse_profile_shell_timeouts(
            "lean",
            _bash_timeouts(1200, 18446744073709551615),
            [("user-toml", _bash_timeouts(1200, 18446744073709551615))],
        )


def test_a_user_timeout_a_project_config_replaces_is_no_clash() -> None:
    refuse_profile_shell_timeouts(
        "lean",
        _bash_timeouts(1200, 1200),
        [
            ("user-toml", _bash_timeouts(120, 300)),
            ("project-toml", _bash_timeouts(1200, 1200)),
        ],
    )


def test_a_project_timeout_over_the_users_is_refused_naming_the_project() -> None:
    with pytest.raises(
        InvalidConfigError,
        match=re.escape(
            "but the project's config.toml sets default_timeout 120 and"
            " max_timeout_seconds 300."
        ),
    ):
        refuse_profile_shell_timeouts(
            "lean",
            _bash_timeouts(1200, 1200),
            [
                ("user-toml", _bash_timeouts(1200, 1200)),
                ("project-toml", _bash_timeouts(120, 300)),
            ],
        )


def test_a_single_config_with_other_timeouts_is_refused_naming_it() -> None:
    with pytest.raises(
        InvalidConfigError,
        match=re.escape(
            "but your config.toml sets default_timeout 120 and max_timeout_seconds 300."
        ),
    ):
        refuse_profile_shell_timeouts(
            "lean",
            _bash_timeouts(1200, 1200),
            [("user-toml", _bash_timeouts(120, 300))],
        )


def test_a_clash_names_the_config_its_effective_timeout_comes_from() -> None:
    # The project replaces only the maximum; the user's 120 s default still runs.
    with pytest.raises(
        InvalidConfigError,
        match=re.escape("but your config.toml sets default_timeout 120. "),
    ):
        refuse_profile_shell_timeouts(
            "lean",
            _bash_timeouts(1200, 1200),
            [
                ("user-toml", _bash_timeouts(120, 300)),
                ("project-toml", {"tools": {"bash": {"max_timeout_seconds": 1200}}}),
            ],
        )


@pytest.mark.asyncio
async def test_lean_runs_when_the_project_config_already_sets_its_timeouts(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    """*Prepare*: A user config with 120 s / 300 s bash timeouts, and a trusted
    project config that sets lean's 1200 s / 1200 s over them.
    *Do*: Start the lean agent.
    *Assert*: It starts, and bash runs with 1200 s / 1200 s, as it does without
    lean.
    """
    # Prepare
    _write_user_config(
        config_dir,
        {
            "installed_agents": ["lean"],
            "tools": {"bash": {"default_timeout": 120, "max_timeout_seconds": 300}},
        },
    )
    project_config = tmp_working_directory / ".vibe" / "config.toml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "[tools.bash]\ndefault_timeout = 1200\nmax_timeout_seconds = 1200\n",
        encoding="utf-8",
    )
    trusted_folders_manager.add_trusted(project_config.parent)
    orchestrator = await build_default_orchestrator()

    # Do
    manager = AgentManager(orchestrator, initial_agent="lean")

    # Assert
    bash = manager.config.tools["bash"]
    assert int(bash["default_timeout"]) == int(bash["max_timeout_seconds"]) == 1200
