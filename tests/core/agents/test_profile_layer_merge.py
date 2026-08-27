from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import PLAN
from vibe.core.agents.registry import apply_profile_overrides
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator

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


def test_ask_profile_only_disables_exit_plan_mode(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    assert _merged_delta(orchestrator, base, "ask") == {
        "disabled_tools": [[], ["exit_plan_mode"]]
    }


def test_plan_profile_scopes_file_tools_to_plans_dir(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    plans_allowlist = PLAN.overrides["tools"]["read_file"]["allowlist"]
    assert _merged_delta(orchestrator, base, "plan") == {
        "tools/edit": [
            "__MISSING__",
            {"permission": "never", "allowlist": plans_allowlist},
        ],
        "tools/read_file": ["__MISSING__", {"allowlist": plans_allowlist}],
        "tools/write_file": [
            "__MISSING__",
            {"permission": "never", "allowlist": plans_allowlist},
        ],
    }


def test_accept_edits_profile_auto_approves_file_edits(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    assert _merged_delta(orchestrator, base, "accept-edits") == {
        "disabled_tools": [[], ["exit_plan_mode"]],
        "tools/edit": ["__MISSING__", {"permission": "always"}],
        "tools/write_file": ["__MISSING__", {"permission": "always"}],
    }


def test_auto_approve_profile_bypasses_permissions(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], base: dict[str, Any]
) -> None:
    assert _merged_delta(orchestrator, base, "auto-approve") == {
        "bypass_tool_permissions": [False, True],
        "disabled_tools": [[], ["exit_plan_mode"]],
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
    assert "exit_plan_mode" in merged.disabled_tools
    assert merged.system_prompt_id == "lean"
