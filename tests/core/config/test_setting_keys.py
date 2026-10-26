from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import tomli_w

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BUILTIN_AGENTS
from vibe.core.config._setting_keys import (
    InvalidStrictConfigError,
    KeyIssueKind,
    UnusedConfigKeysError,
    config_json_schema,
    find_unused_keys,
    notice,
)
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import SuppliedUserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.tools.builtins.web_fetch import WebFetchConfig
from vibe.core.trusted_folders import trusted_folders_manager


def _issues(data: dict[str, Any], layer: str = "user-toml") -> dict[str, KeyIssueKind]:
    return {
        issue.path: issue.kind
        for issue in find_unused_keys(VibeConfigSchema, [(layer, data)])
    }


async def _supplied(data: dict[str, Any]) -> VibeConfigSchema:
    layer = SuppliedUserConfigLayer(data=data)
    orchestrator = await ConfigOrchestrator[VibeConfigSchema].create(
        schema=VibeConfigSchema,
        layers=[layer, EnvironmentLayer(schema=VibeConfigSchema)],
        default_layer_resolver=lambda: layer,
    )
    return orchestrator.config


def test_unknown_top_level_key_is_named_with_a_suggestion() -> None:
    [issue] = find_unused_keys(
        VibeConfigSchema, [("user-toml", {"actiev_model": "x", "api_timeout": 10})]
    )

    assert issue.kind is KeyIssueKind.UNKNOWN
    assert issue.describe() == (
        "`actiev_model` is not a Vibe setting (did you mean `active_model`?)"
    )


def test_unknown_nested_keys_are_named_with_their_path() -> None:
    issues = _issues({
        "session_logging": {"enabeld": False},
        "models": [{"alias": "a", "name": "a", "provider": "p", "temperatrue": 0.1}],
    })

    assert issues == {
        "session_logging.enabeld": KeyIssueKind.UNKNOWN,
        "models[0].temperatrue": KeyIssueKind.UNKNOWN,
    }


def test_unknown_tool_setting_is_named_but_custom_tools_are_left_alone() -> None:
    issues = _issues({
        "tools": {
            "bash": {"permission": "always", "alowlist": ["ls"]},
            "my_mcp_tool": {"anything": 1},
        }
    })

    assert issues == {"tools.bash.alowlist": KeyIssueKind.UNKNOWN}


def test_tool_settings_without_effect_are_flagged() -> None:
    issues = _issues({"tools": {"read_file": {"max_read_bytes": 10}}})

    assert issues == {"tools.read_file.max_read_bytes": KeyIssueKind.NO_EFFECT}


def test_retired_settings_are_flagged_as_no_longer_used() -> None:
    issues = _issues({"include_model_info": False, "include_prompt_detail": False})

    assert issues == {
        "include_model_info": KeyIssueKind.RETIRED,
        "include_prompt_detail": KeyIssueKind.RETIRED,
    }


def test_effective_settings_raise_no_issue() -> None:
    assert not _issues({
        "active_model": "devstral",
        "session_logging": {"enabled": False},
        "tools": {"bash": {"permission": "ask", "allowlist": ["ls"]}},
    })


def test_builtin_agent_profiles_raise_no_issue() -> None:
    for profile in BUILTIN_AGENTS.values():
        assert not _issues(profile.overrides, layer="agent-profile"), profile.name


def test_layers_vibe_writes_are_not_checked() -> None:
    assert not _issues({"not_a_setting": 1}, layer="growthbook")


def test_notice_names_the_layer_and_caps_the_list() -> None:
    issues = find_unused_keys(
        VibeConfigSchema,
        [("project-toml", {f"unknown_{index}": 1 for index in range(8)})],
    )

    [message] = notice(issues)

    assert message.startswith("Settings in the project's .vibe/config.toml")
    assert "`unknown_0`" in message
    assert "and 2 more" in message


@pytest.mark.asyncio
async def test_unknown_key_warns_and_the_config_still_loads() -> None:
    config = await _supplied({"actiev_model": "x", "api_timeout": 12.0})

    assert config.api_timeout == 12.0
    [warning] = config.validation_warnings
    assert "supplied user config" in warning
    assert "`actiev_model`" in warning


@pytest.mark.asyncio
async def test_strict_config_refuses_and_names_every_key() -> None:
    with pytest.raises(UnusedConfigKeysError) as raised:
        await _supplied({
            "strict_config": True,
            "actiev_model": "x",
            "tools": {"web_fetch": {"default_timeout": 5}},
        })

    message = str(raised.value)
    assert "`actiev_model`" in message
    assert "`tools.web_fetch.default_timeout` has no effect" in message


@pytest.mark.asyncio
async def test_strict_config_from_the_environment_refuses() -> None:
    with (
        patch.dict(os.environ, {"VIBE_STRICT_CONFIG": "1"}, clear=True),
        pytest.raises(UnusedConfigKeysError, match="actiev_model"),
    ):
        await _supplied({"actiev_model": "x"})


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["VIBE_STRICT_CONFIG", "vibe_strict_config"])
@pytest.mark.parametrize("value", ["2", "tru", "enabled"])
async def test_a_strict_value_that_is_not_a_boolean_refuses_a_clean_config(
    name: str, value: str
) -> None:
    # The environment layer drops a value it cannot parse, which would turn
    # strict mode off without a word.
    with (
        patch.dict(os.environ, {name: value}, clear=True),
        pytest.raises(InvalidStrictConfigError) as raised,
    ):
        await _supplied({"api_timeout": 12.0})

    assert str(raised.value).startswith(
        f"Config refused because {name}={value!r} is not a boolean."
    )
    assert isinstance(raised.value, UnusedConfigKeysError)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "0", "off", "False", "no"])
async def test_a_strict_value_that_says_off_only_warns(value: str) -> None:
    with patch.dict(os.environ, {"VIBE_STRICT_CONFIG": value}, clear=True):
        config = await _supplied({"actiev_model": "x"})

    assert config.strict_config is False
    assert "`actiev_model`" in config.validation_warnings[0]


@pytest.mark.asyncio
async def test_strict_config_accepts_a_clean_config() -> None:
    config = await _supplied({"strict_config": True, "api_timeout": 12.0})

    assert config.strict_config is True
    assert not config.validation_warnings


_CUSTOM_WEB_FETCH = """
from pydantic import BaseModel

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState


class Args(BaseModel):
    pass


class Result(BaseModel):
    pass


class WebFetchConfig(BaseToolConfig):
    proxy_host: str = ""


class WebFetch(BaseTool[Args, Result, WebFetchConfig, BaseToolState]):
    description = "A custom tool that replaces the builtin web_fetch"

    async def run(self, args, ctx=None):
        yield Result()
"""


@pytest.mark.asyncio
async def test_strict_config_accepts_the_settings_of_a_custom_tool_with_a_builtin_name(
    tmp_path: Path,
) -> None:
    # The custom tool is the one the session runs, so it reads `proxy_host`.
    (tmp_path / "my_web_fetch.py").write_text(_CUSTOM_WEB_FETCH)

    config = await _supplied({
        "strict_config": True,
        "tool_paths": [str(tmp_path)],
        "tools": {"web_fetch": {"proxy_host": "proxy.internal"}},
    })

    assert config.tools["web_fetch"]["proxy_host"] == "proxy.internal"
    assert not config.validation_warnings


@pytest.mark.asyncio
async def test_a_builtin_tool_setting_is_still_checked_beside_a_custom_tool(
    tmp_path: Path,
) -> None:
    (tmp_path / "my_web_fetch.py").write_text(_CUSTOM_WEB_FETCH)

    config = await _supplied({
        "tool_paths": [str(tmp_path)],
        "tools": {"bash": {"alowlist": ["ls"]}},
    })

    assert "`tools.bash.alowlist`" in config.validation_warnings[0]


@pytest.mark.parametrize(
    "not_selected",
    [
        "    selection_priority = -1\n",
        "    @classmethod\n    def is_available(cls, config=None):\n        return False\n",
    ],
    ids=["lower-priority", "unavailable"],
)
@pytest.mark.asyncio
async def test_strict_config_refuses_a_builtin_setting_when_the_custom_tool_is_not_selected(
    tmp_path: Path, not_selected: str
) -> None:
    # The builtin web_fetch runs and never reads `proxy_host`.
    (tmp_path / "my_web_fetch.py").write_text(_CUSTOM_WEB_FETCH + not_selected)

    with pytest.raises(UnusedConfigKeysError, match="tools.web_fetch.proxy_host"):
        await _supplied({
            "strict_config": True,
            "tool_paths": [str(tmp_path)],
            "tools": {"web_fetch": {"proxy_host": "proxy.internal"}},
        })


@pytest.mark.asyncio
async def test_strict_config_sees_the_custom_tool_of_the_session_workspace(
    tmp_path: Path, tmp_working_directory: Path
) -> None:
    # The session runs in another workspace than the process, as an app-server
    # session does, and that workspace's project tool replaces web_fetch.
    workspace = tmp_path / "workspace"
    tools_dir = workspace / ".vibe" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "my_web_fetch.py").write_text(_CUSTOM_WEB_FETCH)
    (workspace / ".vibe" / "config.toml").write_text(
        tomli_w.dumps({
            "strict_config": True,
            "tools": {"web_fetch": {"proxy_host": "proxy.internal"}},
        })
    )
    trusted_folders_manager.add_trusted(workspace)
    session_files = HarnessFilesManager(sources=("user", "project")).for_session(
        workspace
    )

    orchestrator = await build_default_orchestrator(harness_files=session_files)

    assert orchestrator.config.tools["web_fetch"]["proxy_host"] == "proxy.internal"
    assert not orchestrator.config.validation_warnings


def test_a_legacy_name_vibe_still_reads_is_checked_like_its_setting() -> None:
    # `max_inline_chars` is another name for `max_inline_bytes`, which has no
    # effect with the unified harness: left at its default it asks for nothing.
    assert _issues({"tools": {"bash_output": {"max_inline_chars": 30000}}}) == {}
    assert _issues({"tools": {"bash_output": {"max_inline_chars": 1000}}}) == {
        "tools.bash_output.max_inline_chars": KeyIssueKind.NO_EFFECT
    }


def test_config_json_schema_lists_a_legacy_name_vibe_still_reads() -> None:
    tools = config_json_schema(VibeConfigSchema)["properties"]["tools"]["properties"]
    bash_output = tools["bash_output"]["properties"]

    assert bash_output["max_inline_chars"] == bash_output["max_inline_bytes"]


@pytest.mark.asyncio
async def test_project_config_issue_names_the_project_file(
    tmp_working_directory: Path,
) -> None:
    trusted_folders_manager.add_trusted(tmp_working_directory)
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(tomli_w.dumps({"api_timeuot": 1.0}))
    project = ProjectConfigLayer(path=tmp_working_directory)

    orchestrator = await ConfigOrchestrator[VibeConfigSchema].create(
        schema=VibeConfigSchema,
        layers=[project],
        default_layer_resolver=lambda: project,
    )

    [warning] = orchestrator.config.validation_warnings
    assert "project's .vibe/config.toml" in warning
    assert "did you mean `api_timeout`" in warning


def test_config_json_schema_describes_tool_settings() -> None:
    schema = config_json_schema(VibeConfigSchema)

    tools = schema["properties"]["tools"]["properties"]
    bash = tools["bash"]
    assert "allowlist" in bash["properties"]
    assert bash["additionalProperties"] is False
    assert schema["additionalProperties"] is False
    assert tools["read_file"]["properties"]["max_read_bytes"]["deprecated"] is True
    assert tools["task"]["properties"]["permission"]["deprecated"] is True
    assert schema["properties"]["strict_config"]["type"] == "boolean"


# What Vibe up to July 2026 wrote into every new config.toml, cut down.
_OLD_DEFAULT_FILE: dict[str, Any] = {
    "vim_keybindings": False,
    "experiment_overrides": {},
    "include_model_info": True,
    "tools": {
        "read_file": {"permission": "always", "max_read_bytes": 51200},
        "grep": {"sensitive_patterns": ["**/.env", "**/.env.*"]},
        "web_fetch": {"user_agent": WebFetchConfig().user_agent},
        "edit": {"fuzzy_threshold": 0.9},
    },
}


def test_a_file_of_old_written_defaults_raises_no_issue() -> None:
    assert not _issues(_OLD_DEFAULT_FILE)


def test_a_changed_unused_setting_is_still_reported() -> None:
    issues = _issues({
        "vim_keybindings": True,
        "include_model_info": False,
        "tools": {
            "read_file": {"max_read_bytes": 10},
            "edit": {"fuzzy_threshold": 0.5},
            "task": {"permission": "always"},
        },
    })

    assert issues == {
        "vim_keybindings": KeyIssueKind.RETIRED,
        "include_model_info": KeyIssueKind.RETIRED,
        "tools.read_file.max_read_bytes": KeyIssueKind.NO_EFFECT,
        "tools.edit.fuzzy_threshold": KeyIssueKind.RETIRED,
        "tools.task.permission": KeyIssueKind.NO_EFFECT,
    }


def test_strict_config_keeps_a_profile_with_an_unused_key_until_it_is_used(
    tmp_path: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.toml").write_text("api_timeuot = 1.0\n", encoding="utf-8")
    orchestrator = load_orchestrator(
        build_config(strict_config=True, agent_paths=[agents_dir])
    )

    assert "reviewer" in AgentManager(orchestrator).available_agents
    with pytest.raises(UnusedConfigKeysError, match="api_timeuot"):
        AgentManager(orchestrator, initial_agent="reviewer")


def test_a_refused_profile_switch_keeps_the_session_on_its_profile(
    tmp_path: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "reviewer.toml").write_text("api_timeuot = 1.0\n", encoding="utf-8")
    orchestrator = load_orchestrator(
        build_config(strict_config=True, agent_paths=[agents_dir])
    )
    agents = AgentManager(orchestrator)
    previous = agents.active_profile.name

    with pytest.raises(UnusedConfigKeysError, match="api_timeuot"):
        agents.switch_profile("reviewer")

    assert agents.active_profile.name == previous
    orchestrator.rebuild()
