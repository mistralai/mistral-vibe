"""``plugins/local/*``: installed plugins listed and toggled from Desktop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
import tomllib
from typing import Any, cast

import pytest

from tests.stubs.process_services import fake_process_services
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server._session_backend_port import (
    ConnectorAuthAction,
    ResolvedConnector,
    ResolvedConnectorCatalog,
)
from vibe.app_server.connector_catalog import ConnectorCatalogService
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
    starter_prompt: dict[str, object] | None = None,
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
    if starter_prompt is not None:
        extension["starterPrompt"] = starter_prompt
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
    assert plugin.starter_prompt is None


@pytest.mark.asyncio
async def test_lists_a_plugins_starter_prompt_for_try_in_chat(config_dir: Path) -> None:
    _mount(
        config_dir / "plugins" / "productivity",
        starter_prompt={"prompt": {"en": "Plan my week", "fr": "Planifie ma semaine"}},
    )
    _mount(
        config_dir / "plugins" / "briefing", "briefing", starter_prompt={"submit": True}
    )

    listed = {p.name: p for p in (await _list(_handler())).plugins}

    typed = listed["productivity"].starter_prompt
    assert typed is not None
    assert typed.prompt == {"en": "Plan my week", "fr": "Planifie ma semaine"}
    assert typed.submit is False
    sent = listed["briefing"].starter_prompt
    assert sent is not None
    assert (sent.prompt, sent.submit) == (None, True)
    # Desktop shows it now: no longer a part it does not support.
    assert listed["productivity"].unsupported_parts == []


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
    assert cast(dict[str, Any], caught.value.data) == {
        "reason": "namespace_collision",
        "collidesWith": ["reports"],
    }
    assert "'reports'" in str(caught.value)
    assert _user_disabled(config_dir) == ["reports-old"]


@pytest.mark.asyncio
async def test_turning_a_plugin_on_can_turn_off_the_ones_under_its_namespace(
    config_dir: Path,
) -> None:
    _mount(config_dir / "plugins" / "reports", name="reports", namespace="shared")
    _mount(
        config_dir / "plugins" / "reports-old",
        name="reports-old",
        namespace="shared",
        default_enabled=False,
    )
    _mount(config_dir / "plugins" / "other", name="other", namespace="elsewhere")
    config = config_dir / "config.toml"
    config.write_text('disabled_plugins = ["reports-old"]\n' + config.read_text())
    handler = _handler()

    result = await handler.dispatch(
        "plugins/local/setEnabled",
        {"name": "reports-old", "enabled": True, "turnOffCollisions": True},
    )
    response = cast(PluginsLocalSetEnabledResponse, result.response)
    listed = {
        p.name: (p.enabled, p.collides_with) for p in (await _list(handler)).plugins
    }

    assert response.turned_off == ["reports"]
    assert (response.plugin.enabled, response.plugin.collides_with) == (True, [])
    assert listed == {
        "other": (True, []),
        "reports": (False, []),
        "reports-old": (True, []),
    }
    assert _user_disabled(config_dir) == ["reports"]
    assert _user_enabled(config_dir) == ["reports-old"]


@pytest.mark.asyncio
async def test_turning_a_plugin_on_turns_off_an_off_by_default_one_it_collides_with(
    config_dir: Path,
) -> None:
    _mount(
        config_dir / "plugins" / "reports",
        name="reports",
        namespace="shared",
        default_enabled=False,
    )
    _mount(
        config_dir / "plugins" / "reports-old",
        name="reports-old",
        namespace="shared",
        default_enabled=False,
    )
    config = config_dir / "config.toml"
    config.write_text('enabled_plugins = ["reports"]\n' + config.read_text())
    handler = _handler()

    result = await handler.dispatch(
        "plugins/local/setEnabled",
        {"name": "reports-old", "enabled": True, "turnOffCollisions": True},
    )

    assert cast(PluginsLocalSetEnabledResponse, result.response).turned_off == [
        "reports"
    ]
    assert _user_enabled(config_dir) == ["reports-old"]
    assert _user_disabled(config_dir) == ["reports"]


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
async def test_a_plugin_shipping_several_formats_lists_without_issues(
    config_dir: Path,
) -> None:
    root = config_dir / "plugins" / "superpowers"
    skill = root / "skills" / "brainstorming" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: brainstorming\ndescription: Explore.\n---\n\nb\n")
    for marker in (".claude-plugin", ".codex-plugin"):
        (root / marker).mkdir()
        (root / marker / "plugin.json").write_text(json.dumps({"name": "superpowers"}))

    listed = await _list(_handler())

    assert [plugin.name for plugin in listed.plugins] == ["superpowers"]
    assert listed.issues == []


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


def _mount_with_connectors(destination: Path, connectors: dict[str, list[str]]) -> Path:
    """A plugin whose ``connectors.json`` needs ``connectors``, in that order."""
    _mount(destination, namespace="productivity")
    (destination / "connectors.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "connectors": [
                {"id": source_id, "tools": tools}
                for source_id, tools in connectors.items()
            ],
        })
    )
    return destination


def _catalog_connector(
    alias: str, *, ready: bool, auth_action: ConnectorAuthAction = "none"
) -> ResolvedConnector:
    return ResolvedConnector(
        raw_id=f"id-{alias}",
        alias=alias,
        display_name=alias.capitalize(),
        ready=ready,
        auth_action=auth_action,
        tools=(),
    )


def _handler_with_catalog(
    read: Callable[[Any], Awaitable[ResolvedConnectorCatalog | None]],
) -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user", "project")),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
        worktrees=fake_process_services().worktrees,
        experiments=fake_process_services().experiments,
        model_catalog=fake_process_services().model_catalog,
        connector_catalog=read,
    )


@pytest.mark.asyncio
async def test_lists_the_connectors_a_plugin_needs_in_its_order(
    config_dir: Path,
) -> None:
    _mount_with_connectors(
        config_dir / "plugins" / "productivity",
        {"slack": ["send_message"], "jira": ["create_issue"]},
    )
    catalog = ResolvedConnectorCatalog(
        provider_fingerprint="account",
        revision="r1",
        connectors=(_catalog_connector("slack", ready=True),),
    )

    async def read(_orchestrator: Any) -> ResolvedConnectorCatalog:
        return catalog

    [plugin] = (await _list(_handler_with_catalog(read))).plugins

    assert [
        (c.id, c.display_name, c.connector_id, c.status, c.tools)
        for c in plugin.connectors
    ] == [
        ("slack", "Slack", "id-slack", "connected", ["send_message"]),
        # Not in the account's catalog at all.
        ("jira", None, None, "unavailable", ["create_issue"]),
    ]


@pytest.mark.parametrize(
    ("ready", "auth_action", "status"),
    [
        (True, "none", "connected"),
        (True, "oauth", "connected"),
        (False, "oauth", "not_connected"),
        (False, "credentials_setup", "not_connected"),
        (False, "no_auth_connect", "not_connected"),
        (False, "none", "unavailable"),
        (False, "unknown", "unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_a_connector_status_follows_what_the_user_must_do(
    config_dir: Path, ready: bool, auth_action: ConnectorAuthAction, status: str
) -> None:
    _mount_with_connectors(
        config_dir / "plugins" / "productivity", {"slack": ["send_message"]}
    )
    catalog = ResolvedConnectorCatalog(
        provider_fingerprint="account",
        revision="r1",
        connectors=(_catalog_connector("slack", ready=ready, auth_action=auth_action),),
    )

    async def read(_orchestrator: Any) -> ResolvedConnectorCatalog:
        return catalog

    [plugin] = (await _list(_handler_with_catalog(read))).plugins

    assert [c.status for c in plugin.connectors] == [status]


@pytest.mark.asyncio
async def test_connectors_are_unknown_while_no_catalog_is_held(
    config_dir: Path,
) -> None:
    """Signed out, connectors off, or not fetched yet: the reader holds none."""
    _mount_with_connectors(
        config_dir / "plugins" / "productivity", {"slack": ["send_message"]}
    )

    async def read(_orchestrator: Any) -> None:
        return None

    [plugin] = (await _list(_handler_with_catalog(read))).plugins

    assert [(c.id, c.status) for c in plugin.connectors] == [("slack", "unknown")]


@pytest.mark.asyncio
async def test_a_catalog_that_fails_to_read_leaves_the_list_and_says_unknown(
    config_dir: Path,
) -> None:
    _mount_with_connectors(
        config_dir / "plugins" / "productivity", {"slack": ["send_message"]}
    )

    async def read(_orchestrator: Any) -> ResolvedConnectorCatalog:
        raise RuntimeError("the disk cache is unreadable")

    [plugin] = (await _list(_handler_with_catalog(read))).plugins

    assert [(c.id, c.status) for c in plugin.connectors] == [("slack", "unknown")]


@pytest.mark.asyncio
async def test_a_plugin_without_connectors_lists_none(config_dir: Path) -> None:
    _mount(config_dir / "plugins" / "productivity")

    [plugin] = (await _list(_handler())).plugins

    assert plugin.connectors == []


@pytest.mark.asyncio
async def test_the_status_follows_the_catalog_without_a_reset(config_dir: Path) -> None:
    """Connecting, then signing out: each list reads the catalog held then."""
    _mount_with_connectors(
        config_dir / "plugins" / "productivity", {"slack": ["send_message"]}
    )
    held: list[ResolvedConnectorCatalog | None] = [
        ResolvedConnectorCatalog(
            provider_fingerprint="account",
            revision="r1",
            connectors=(_catalog_connector("slack", ready=False, auth_action="oauth"),),
        )
    ]

    async def read(_orchestrator: Any) -> ResolvedConnectorCatalog | None:
        return held[0]

    handler = _handler_with_catalog(read)
    statuses = [(await _list(handler)).plugins[0].connectors[0].status]
    held[0] = ResolvedConnectorCatalog(
        provider_fingerprint="account",
        revision="r2",
        connectors=(_catalog_connector("slack", ready=True),),
    )
    statuses.append((await _list(handler)).plugins[0].connectors[0].status)
    held[0] = None
    statuses.append((await _list(handler)).plugins[0].connectors[0].status)

    assert statuses == ["not_connected", "connected", "unknown"]


@pytest.mark.asyncio
async def test_toggling_a_plugin_answers_its_connectors_too(config_dir: Path) -> None:
    _mount_with_connectors(
        config_dir / "plugins" / "productivity", {"slack": ["send_message"]}
    )
    catalog = ResolvedConnectorCatalog(
        provider_fingerprint="account",
        revision="r1",
        connectors=(_catalog_connector("slack", ready=True),),
    )

    async def read(_orchestrator: Any) -> ResolvedConnectorCatalog:
        return catalog

    plugin = await _toggle(
        _handler_with_catalog(read), name="productivity", enabled=False
    )

    assert [(c.id, c.status) for c in plugin.connectors] == [("slack", "connected")]


@pytest.mark.asyncio
async def test_the_plugins_list_reads_the_catalog_the_process_holds(
    process_services: ProcessServices, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app server's own wiring: the list reads its connector service."""
    _mount_with_connectors(
        config_dir / "plugins" / "productivity", {"slack": ["send_message"]}
    )
    reads: list[object] = []

    async def read_cached_catalog(
        _self: ConnectorCatalogService, orchestrator: object
    ) -> ResolvedConnectorCatalog:
        reads.append(orchestrator)
        return ResolvedConnectorCatalog(
            provider_fingerprint="account",
            revision="r1",
            connectors=(_catalog_connector("slack", ready=True),),
        )

    monkeypatch.setattr(
        ConnectorCatalogService, "read_cached_catalog", read_cached_catalog
    )
    process = HarnessProcess(services=process_services)
    try:
        listed = await _list(cast(HostRequestHandler, process.host_handler))
    finally:
        await process.close()

    [plugin] = [p for p in listed.plugins if p.name == "productivity"]
    assert [(c.id, c.status) for c in plugin.connectors] == [("slack", "connected")]
    assert len(reads) == 1
