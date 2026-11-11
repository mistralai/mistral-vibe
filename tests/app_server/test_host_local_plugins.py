"""``plugins/local/*``: installed plugins listed and toggled from Desktop."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Any, cast

import pytest

from tests.stubs.process_services import fake_process_services
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server.protocol import (
    LocalPluginSummary,
    PluginsLocalListResponse,
    PluginsLocalSetEnabledResponse,
    ProtocolErrorCode,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.plugins import NativePluginResolver

_SKILLS = {
    "inspect-data": "Inspect a dataset and identify important patterns.",
    "prepare-report": "Prepare a concise report from source material.",
}


def _mount(
    destination: Path,
    name: str = "productivity",
    namespace: str | None = None,
    *,
    default_enabled: bool = True,
) -> Path:
    destination.mkdir(parents=True)
    manifest: dict[str, object] = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name,
        "version": "1.0.0",
        "description": "Reusable productivity workflows.",
    }
    extension: dict[str, object] = {"schemaVersion": 1}
    if namespace is not None:
        extension["toolNamespace"] = namespace
    if not default_enabled:
        extension["defaultEnabled"] = False
    if len(extension) > 1:
        manifest["extensions"] = {"ai.mistral.vibe": extension}
    (destination / "plugin.json").write_text(json.dumps(manifest))
    for name, description in _SKILLS.items():
        skill = destination / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\nb\n")
    return destination


def _handler(builtin_roots: list[Path] | None = None) -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user", "project")),
        builtin_plugin_roots=builtin_roots or [],
        org_config=fake_process_services().org_config,
        worktrees=fake_process_services().worktrees,
        experiments=fake_process_services().experiments,
        model_catalog=fake_process_services().model_catalog,
    )


async def _list(handler: HostRequestHandler, **params: Any) -> PluginsLocalListResponse:
    result = await handler.dispatch("plugins/local/list", params)
    return cast(PluginsLocalListResponse, result.response)


async def _toggle(handler: HostRequestHandler, **params: Any) -> LocalPluginSummary:
    result = await handler.dispatch("plugins/local/setEnabled", params)
    return cast(PluginsLocalSetEnabledResponse, result.response).plugin


def _user_disabled(config_dir: Path) -> list[str]:
    config = tomllib.loads((config_dir / "config.toml").read_text())
    return list(config.get("disabled_plugins", []))


def _user_enabled(config_dir: Path) -> list[str]:
    config = tomllib.loads((config_dir / "config.toml").read_text())
    return list(config.get("enabled_plugins", []))


@pytest.mark.asyncio
async def test_lists_a_global_plugin_with_what_it_provides(config_dir: Path) -> None:
    _mount(config_dir / "plugins" / "productivity")

    listed = await _list(_handler())

    [plugin] = listed.plugins
    assert plugin.name == "productivity"
    assert plugin.scope == "global"
    assert plugin.version == "1.0.0"
    assert plugin.source_format == "agent_plugins_1_0"
    assert plugin.enabled is True
    assert plugin.locked is False
    assert sorted(
        (c.name, c.description) for c in plugin.components if c.kind == "skill"
    ) == [
        ("productivity:inspect-data", _SKILLS["inspect-data"]),
        ("productivity:prepare-report", _SKILLS["prepare-report"]),
    ]


@pytest.mark.asyncio
async def test_lists_built_in_plugins_from_the_roots_the_host_was_given(
    tmp_path: Path,
) -> None:
    builtins = tmp_path / "builtins"
    _mount(builtins / "productivity")

    listed = await _list(_handler([builtins]))

    assert [(p.name, p.scope) for p in listed.plugins] == [("productivity", "builtin")]


@pytest.mark.asyncio
async def test_disabling_keeps_the_plugin_listed_and_out_of_sessions(
    config_dir: Path,
) -> None:
    _mount(config_dir / "plugins" / "productivity")
    handler = _handler()

    toggled = await _toggle(handler, name="productivity", enabled=False)

    assert toggled.enabled is False
    assert _user_disabled(config_dir) == ["productivity"]
    [listed] = (await _list(handler)).plugins
    assert listed.enabled is False
    assert listed.components
    resolution = NativePluginResolver(
        user_roots=[config_dir / "plugins"],
        config_orchestrator=await handler._load_orchestrator(
            HarnessFilesManager(sources=("user",))
        ),
    ).resolve()
    assert resolution.plugins == ()
    assert dict(resolution.skills) == {}


@pytest.mark.asyncio
async def test_enabling_again_removes_the_name_from_the_users_config(
    config_dir: Path,
) -> None:
    _mount(config_dir / "plugins" / "productivity")
    handler = _handler()
    await _toggle(handler, name="productivity", enabled=False)

    toggled = await _toggle(handler, name="productivity", enabled=True)

    assert toggled.enabled is True
    assert _user_disabled(config_dir) == []


@pytest.mark.asyncio
async def test_lists_a_projects_plugins_only_when_asked_for_that_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    _mount(project / ".vibe" / "plugins" / "productivity")
    handler = _handler()

    user_level = await _list(handler)
    project_level = await _list(handler, projectRoot=str(project), trustWorkspace=True)

    assert user_level.plugins == []
    assert [(p.name, p.scope) for p in project_level.plugins] == [
        ("productivity", "project")
    ]


@pytest.mark.asyncio
async def test_a_plugin_another_layer_disables_is_locked(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _mount(project / ".vibe" / "plugins" / "productivity")
    (project / ".vibe" / "config.toml").write_text(
        'disabled_plugins = ["productivity"]\n'
    )
    handler = _handler()

    [plugin] = (
        await _list(handler, projectRoot=str(project), trustWorkspace=True)
    ).plugins
    with pytest.raises(RequestFailure) as caught:
        await _toggle(
            handler,
            name="productivity",
            enabled=True,
            projectRoot=str(project),
            trustWorkspace=True,
        )

    assert plugin.enabled is False
    assert plugin.locked is True
    assert caught.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert cast(dict[str, Any], caught.value.data)["reason"] == "locked"


@pytest.mark.asyncio
async def test_a_plugin_the_user_and_another_layer_disable_is_locked(
    tmp_path: Path, config_dir: Path
) -> None:
    project = tmp_path / "repo"
    _mount(project / ".vibe" / "plugins" / "productivity")
    (project / ".vibe" / "config.toml").write_text(
        'disabled_plugins = ["productivity"]\n'
    )
    config = config_dir / "config.toml"
    config.write_text('disabled_plugins = ["productivity"]\n' + config.read_text())
    handler = _handler()

    [plugin] = (
        await _list(handler, projectRoot=str(project), trustWorkspace=True)
    ).plugins
    with pytest.raises(RequestFailure) as caught:
        await _toggle(
            handler,
            name="productivity",
            enabled=True,
            projectRoot=str(project),
            trustWorkspace=True,
        )

    assert plugin.enabled is False
    assert plugin.locked is True
    assert caught.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert cast(dict[str, Any], caught.value.data)["reason"] == "locked"
    assert _user_disabled(config_dir) == ["productivity"]


@pytest.mark.asyncio
async def test_a_plugin_sharing_the_namespace_of_one_that_is_on_stays_off(
    config_dir: Path,
) -> None:
    _mount(config_dir / "plugins" / "reports", name="reports", namespace="shared")
    _mount(
        config_dir / "plugins" / "reports-old", name="reports-old", namespace="shared"
    )
    config = config_dir / "config.toml"
    config.write_text('disabled_plugins = ["reports-old"]\n' + config.read_text())
    handler = _handler()

    listed = await _list(handler)
    with pytest.raises(RequestFailure) as caught:
        await _toggle(handler, name="reports-old", enabled=True)

    assert {(p.name, p.enabled) for p in listed.plugins} == {
        ("reports", True),
        ("reports-old", False),
    }
    assert {
        p.name: sorted(c.name for c in p.components if c.kind == "skill")
        for p in listed.plugins
    } == {
        "reports": ["reports:inspect-data", "reports:prepare-report"],
        "reports-old": ["reports-old:inspect-data", "reports-old:prepare-report"],
    }
    assert caught.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert cast(dict[str, Any], caught.value.data)["reason"] == "namespace_collision"
    assert "'reports'" in str(caught.value)
    assert _user_disabled(config_dir) == ["reports-old"]


@pytest.mark.asyncio
async def test_two_plugins_on_under_one_namespace_stay_listed_to_turn_one_off(
    config_dir: Path,
) -> None:
    _mount(config_dir / "plugins" / "reports", name="reports", namespace="shared")
    _mount(
        config_dir / "plugins" / "reports-fork", name="reports-fork", namespace="shared"
    )
    handler = _handler()

    listed = await _list(handler)
    turned_off = await _toggle(handler, name="reports-fork", enabled=False)
    [kept, off] = (await _list(handler)).plugins

    assert {(p.name, p.enabled, p.locked) for p in listed.plugins} == {
        ("reports", True, False),
        ("reports-fork", True, False),
    }
    assert {p.name: p.collides_with for p in listed.plugins} == {
        "reports": ["reports-fork"],
        "reports-fork": ["reports"],
    }
    assert any("collides" in issue.message for issue in listed.issues)
    assert (turned_off.enabled, turned_off.collides_with) == (False, [])
    assert (kept.name, kept.enabled, kept.collides_with) == ("reports", True, [])
    assert (off.name, off.enabled) == ("reports-fork", False)


@pytest.mark.asyncio
async def test_vibe_own_plugin_cannot_be_turned_off(
    tmp_path: Path, config_dir: Path
) -> None:
    builtins = tmp_path / "builtins"
    _mount(builtins / "vibe", name="vibe")
    config = config_dir / "config.toml"
    config.write_text('disabled_plugins = ["vibe"]\n' + config.read_text())
    handler = _handler([builtins])

    [plugin] = (await _list(handler)).plugins
    with pytest.raises(RequestFailure) as caught:
        await _toggle(handler, name="vibe", enabled=False)
    resolution = NativePluginResolver(
        builtin_roots=[builtins],
        data_root_base=tmp_path / "data",
        config_orchestrator=await handler._load_orchestrator(
            HarnessFilesManager(sources=("user",))
        ),
    ).resolve()

    assert (plugin.enabled, plugin.locked, plugin.required) == (True, True, True)
    assert caught.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert [p.name for p in resolution.plugins] == ["vibe"]


@pytest.mark.asyncio
async def test_an_off_by_default_plugin_starts_off_and_turns_on(
    tmp_path: Path, config_dir: Path
) -> None:
    builtins = tmp_path / "builtins"
    _mount(builtins / "computer-use", name="computer-use", default_enabled=False)
    handler = _handler([builtins])

    [listed] = (await _list(handler)).plugins
    turned_on = await _toggle(handler, name="computer-use", enabled=True)
    on_config = (_user_enabled(config_dir), _user_disabled(config_dir))
    resolution = NativePluginResolver(
        builtin_roots=[builtins],
        data_root_base=tmp_path / "data",
        config_orchestrator=await handler._load_orchestrator(
            HarnessFilesManager(sources=("user",))
        ),
    ).resolve()
    turned_off = await _toggle(handler, name="computer-use", enabled=False)

    assert (listed.enabled, listed.locked) == (False, False)
    assert turned_on.enabled is True
    assert on_config == (["computer-use"], [])
    assert [p.name for p in resolution.plugins] == ["computer-use"]
    assert turned_off.enabled is False
    assert (_user_enabled(config_dir), _user_disabled(config_dir)) == (
        [],
        ["computer-use"],
    )


@pytest.mark.asyncio
async def test_an_off_by_default_plugin_stays_out_of_sessions(tmp_path: Path) -> None:
    builtins = tmp_path / "builtins"
    _mount(builtins / "computer-use", name="computer-use", default_enabled=False)
    handler = _handler([builtins])

    resolution = NativePluginResolver(
        builtin_roots=[builtins],
        data_root_base=tmp_path / "data",
        config_orchestrator=await handler._load_orchestrator(
            HarnessFilesManager(sources=("user",))
        ),
    ).resolve()

    assert resolution.plugins == ()


@pytest.mark.asyncio
async def test_a_plugin_another_layer_turns_on_can_still_be_turned_off(
    tmp_path: Path, config_dir: Path
) -> None:
    project = tmp_path / "repo"
    _mount(
        project / ".vibe" / "plugins" / "computer-use",
        name="computer-use",
        default_enabled=False,
    )
    (project / ".vibe" / "config.toml").write_text(
        'enabled_plugins = ["computer-use"]\n'
    )
    handler = _handler()
    location = {"projectRoot": str(project), "trustWorkspace": True}

    [listed] = (await _list(handler, **location)).plugins
    turned_off = await _toggle(handler, name="computer-use", enabled=False, **location)

    assert (listed.enabled, listed.locked) == (True, False)
    assert (turned_off.enabled, turned_off.locked) == (False, False)
    assert _user_disabled(config_dir) == ["computer-use"]


@pytest.mark.asyncio
async def test_toggling_an_unknown_plugin_is_not_found() -> None:
    with pytest.raises(RequestFailure) as caught:
        await _toggle(_handler(), name="missing", enabled=False)

    assert caught.value.code is ProtocolErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_reports_a_plugin_that_fails_to_resolve(config_dir: Path) -> None:
    broken = config_dir / "plugins" / "broken"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text("{not json")

    listed = await _list(_handler())

    assert listed.plugins == []
    assert any(issue.file.startswith(str(broken)) for issue in listed.issues)


@pytest.mark.asyncio
async def test_an_untrusted_projects_plugins_are_not_listed(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    _mount(project / ".vibe" / "plugins" / "productivity")

    listed = await _list(_handler(), projectRoot=str(project), trustWorkspace=False)

    assert listed.plugins == []


@pytest.mark.asyncio
async def test_turning_on_an_off_by_default_plugin_writes_both_lists_at_once(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.core.config.orchestrator import ConfigOrchestrator

    builtins = tmp_path / "builtins"
    _mount(builtins / "computer-use", name="computer-use", default_enabled=False)
    config = config_dir / "config.toml"
    config.write_text('disabled_plugins = ["computer-use"]\n' + config.read_text())
    handler = _handler([builtins])
    writes: list[list[str]] = []
    mutate_fields = ConfigOrchestrator.mutate_fields

    async def record(self: Any, mutations: Any, *args: Any, **kwargs: Any) -> Any:
        writes.append(sorted(mutations))
        return await mutate_fields(self, mutations, *args, **kwargs)

    monkeypatch.setattr(ConfigOrchestrator, "mutate_fields", record)

    await _toggle(handler, name="computer-use", enabled=True)

    assert writes == [["/disabled_plugins", "/enabled_plugins"]]
    assert (_user_enabled(config_dir), _user_disabled(config_dir)) == (
        ["computer-use"],
        [],
    )
