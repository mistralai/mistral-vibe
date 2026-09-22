"""Host-owned, secret-free child configuration materialization."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import (
    RustDisabledRuntimeToolFeature,
    RustHarnessCapabilitySet,
    RustHarnessConfig,
)
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._storage import sha256_json
from mistralai_vibe_local_harness.vibe._connector_models import (
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
)
from mistralai_vibe_local_harness.vibe._mcp_models import ResolvedMCPCatalog
from mistralai_vibe_local_harness.vibe._subagents._host import ResolvedChildSessionBinding
from mistralai_vibe_local_harness.vibe._subagents._models import (
    MAX_DECLARED_AGENT_TYPES,
    DeclaredAgentTypeProfile,
    ResolvedContentGrant,
    ResolvedHookGrant,
    ResolvedSourceGrant,
    ResolvedSubagentPolicyCeiling,
    ResolvedToolGrant,
)

logger = logging.getLogger(__name__)

# ``classify`` (smart approve) is a gating mode like ``ask``: it caps a child at the
# classifier gate, but a ``deny`` ceiling still wins.
_MODE_STRENGTH: dict[Literal["allow", "ask", "deny", "classify"], int] = {
    "deny": 0,
    "ask": 1,
    "classify": 1,
    "allow": 2,
}


@dataclass(frozen=True, slots=True)
class LocalChildSessionBinding(ResolvedChildSessionBinding):
    core_config: RustHarnessConfig
    adapter_config: LocalRuntimeAdapterConfig
    integrations_enabled: bool


@dataclass(frozen=True, slots=True)
class ResolvedSubagentConfiguration:
    root_config: RustHarnessConfig
    policy_ceiling: ResolvedSubagentPolicyCeiling
    bindings: dict[str | None, LocalChildSessionBinding]


def resolve_subagent_configuration(
    config: RustHarnessConfig,
    adapter: LocalRuntimeAdapterConfig,
    *,
    materialization_root: Path,
    mcp_catalog: ResolvedMCPCatalog | None = None,
    connector_catalog: ResolvedConnectorCatalog | None = None,
    connector_selection: ResolvedConnectorSelection | None = None,
    connector_gateway_authority_digest: str | None = None,
) -> ResolvedSubagentConfiguration:
    """Resolve the generic subagent profile beneath one parent ceiling."""

    integration_authority = _integration_authority(
        mcp_catalog=mcp_catalog,
        connector_catalog=connector_catalog,
        connector_selection=connector_selection,
        connector_gateway_authority_digest=connector_gateway_authority_digest,
    )
    policy = _policy_ceiling(config, adapter, integration_authority=integration_authority)
    policy_digest = _policy_ceiling_digest(policy)
    generic_config = _child_core_config(config, system_instructions=config.system_instructions)
    generic_adapter = replace(adapter, env=dict(adapter.env), tool_modes=dict(adapter.tool_modes))
    generic = LocalChildSessionBinding(
        template_digest=_binding_digest(
            generic_config,
            generic_adapter,
            profile="generic",
            integration_authority=integration_authority,
        ),
        policy_ceiling_digest=policy_digest,
        core_config=generic_config,
        adapter_config=generic_adapter,
        integrations_enabled=True,
    )

    bindings: dict[str | None, LocalChildSessionBinding] = {None: generic}
    root = advertise_bound_agent_types(config, bindings)
    return ResolvedSubagentConfiguration(
        root_config=root,
        policy_ceiling=policy,
        bindings=bindings,
    )


def resolve_declared_agent_types(
    configuration: ResolvedSubagentConfiguration,
    adapter: LocalRuntimeAdapterConfig,
    profiles: Sequence[DeclaredAgentTypeProfile],
) -> ResolvedSubagentConfiguration:
    # The ceiling is reused verbatim, digest included: a declared profile is a point
    # inside the envelope the Host resolved, so a session can extend its own table
    # without the ceiling moving under a controller that has already pinned it.
    if not profiles:
        return configuration

    bindings = dict(configuration.bindings)
    policy_digest = _policy_ceiling_digest(configuration.policy_ceiling)
    for profile in profiles:
        if profile.agent_type in bindings:
            logger.warning(
                "Ignored declared agent type %r: the name is already bound",
                profile.agent_type,
            )
            continue
        if len(bindings) - 1 >= MAX_DECLARED_AGENT_TYPES:
            logger.warning(
                "Ignored declared agent type %r: more than %d agent types were declared",
                profile.agent_type,
                MAX_DECLARED_AGENT_TYPES,
            )
            continue
        bindings[profile.agent_type] = _declared_binding(
            configuration.root_config,
            adapter,
            profile,
            policy_digest=policy_digest,
        )
    return ResolvedSubagentConfiguration(
        root_config=configuration.root_config,
        policy_ceiling=configuration.policy_ceiling,
        bindings=bindings,
    )


def _declared_binding(
    config: RustHarnessConfig,
    adapter: LocalRuntimeAdapterConfig,
    profile: DeclaredAgentTypeProfile,
    *,
    policy_digest: str,
) -> LocalChildSessionBinding:
    child_config = _child_core_config(
        config,
        system_instructions=(
            profile.instructions if profile.instructions is not None else config.system_instructions
        ),
    )
    child_config = child_config.model_copy(
        update={
            "capabilities": child_config.capabilities.model_copy(
                update={"tool_groups": []}, deep=True
            ),
            "plugins": [],
        },
        deep=True,
    )
    child_adapter = replace(
        adapter,
        env=dict(adapter.env),
        tool_modes={
            name: _narrow(mode, profile.tool_ceiling.get(name, "deny"))
            for name, mode in adapter.tool_modes.items()
        },
    )
    return LocalChildSessionBinding(
        template_digest=_binding_digest(
            child_config,
            child_adapter,
            profile=f"declared:{profile.authority_digest}:{profile.agent_type}",
            integration_authority=None,
        ),
        policy_ceiling_digest=policy_digest,
        core_config=child_config,
        adapter_config=child_adapter,
        integrations_enabled=False,
    )


def _narrow(
    parent: Literal["allow", "ask", "deny", "classify"],
    declared: Literal["allow", "ask", "deny"],
) -> Literal["allow", "ask", "deny", "classify"]:
    return parent if _MODE_STRENGTH[parent] <= _MODE_STRENGTH[declared] else declared


def advertise_bound_agent_types(
    config: RustHarnessConfig,
    bindings: dict[str | None, LocalChildSessionBinding],
) -> RustHarnessConfig:
    """Retain only agent types backed by an executable child-session binding."""
    bound_names = {name for name in bindings if name is not None}

    def filter_capabilities(
        capabilities: RustHarnessCapabilitySet,
    ) -> RustHarnessCapabilitySet:
        return capabilities.model_copy(
            update={
                "agent_types": [
                    definition
                    for definition in capabilities.agent_types
                    if definition.name in bound_names
                ]
            },
            deep=True,
        )

    return config.model_copy(
        update={
            "capabilities": filter_capabilities(config.capabilities),
            "plugins": [
                plugin.model_copy(
                    update={"capabilities": filter_capabilities(plugin.capabilities)},
                    deep=True,
                )
                for plugin in config.plugins
            ],
        },
        deep=True,
    )


def child_config(
    binding: ResolvedChildSessionBinding, session_id: str
) -> tuple[RustHarnessConfig, LocalRuntimeAdapterConfig]:
    if not isinstance(binding, LocalChildSessionBinding):
        raise TypeError("local child Host requires a local child binding")
    return (
        binding.core_config.model_copy(update={"task_id": session_id}, deep=True),
        replace(binding.adapter_config, env=dict(binding.adapter_config.env)),
    )


def _child_core_config(config: RustHarnessConfig, *, system_instructions: str) -> RustHarnessConfig:
    tools = config.settings.tools.model_copy(
        update={"subagents": RustDisabledRuntimeToolFeature()}, deep=True
    )
    settings = config.settings.model_copy(update={"tools": tools}, deep=True)
    capabilities = config.capabilities.model_copy(update={"agent_types": []}, deep=True)
    return config.model_copy(
        update={
            "system_instructions": system_instructions,
            "settings": settings,
            "capabilities": capabilities,
        },
        deep=True,
    )


def _policy_ceiling(
    config: RustHarnessConfig,
    adapter: LocalRuntimeAdapterConfig,
    *,
    integration_authority: dict[str, JsonValue] | None,
) -> ResolvedSubagentPolicyCeiling:
    roots = sorted(
        str(path.expanduser().resolve()) for path in (adapter.workspace_roots or (adapter.cwd,))
    )
    tool_grants = {
        name: ResolvedToolGrant(
            binding_id=f"builtin:{name}",
            contract_digest=_digest({"name": name}),
            permission=_permission(mode),
        )
        for name, mode in sorted(adapter.tool_modes.items())
    }
    skill_grants = {
        name: ResolvedContentGrant(
            binding_id=f"skill:{name}",
            content_digest=_digest(content),
            allowed_tool_names=[],
        )
        for name, content in sorted(adapter.skills.items())
    }
    source_grants = {
        group.name: ResolvedSourceGrant(
            binding_id=f"provided:{group.name}",
            authority_digest=_digest(
                cast(
                    JsonValue,
                    group.model_dump(mode="json", by_alias=True, exclude_none=True),
                )
            ),
            model_access="programmatic",
            allowed_tool_names=sorted(tool.name for tool in group.tools),
        )
        for group in sorted(config.capabilities.tool_groups, key=lambda item: item.name)
    }
    if integration_authority is not None:
        for server in cast(list[dict[str, JsonValue]], integration_authority["mcp"]):
            name = cast(str, server["name"])
            source_grants[f"mcp:{name}"] = ResolvedSourceGrant(
                binding_id=f"mcp:{name}",
                authority_digest=cast(str, server["authority_digest"]),
                model_access="both",
                allowed_tool_names=cast(list[str], server["allowed_tool_names"]),
            )
    hook_grants = {
        hook.id: ResolvedHookGrant(
            binding_id=hook.id,
            hook_type=hook.point,
            matcher_digest=_digest(
                cast(
                    JsonValue,
                    hook.selector.model_dump(mode="json", by_alias=True, exclude_none=True),
                )
            ),
            mandatory=True,
        )
        for hook in sorted(config.capabilities.hook_bindings, key=lambda item: item.id)
    }
    completion_authority = {
        "provider": adapter.provider,
        "base_url": adapter.base_url,
        "model": adapter.model,
        "temperature": adapter.temperature,
        "timeout_s": adapter.timeout_s,
        "retry_max_elapsed_time_s": adapter.retry_max_elapsed_time_s,
    }
    return ResolvedSubagentPolicyCeiling(
        workdir=str(adapter.cwd.expanduser().resolve()),
        read_roots=roots,
        write_roots=roots,
        completion_grants={"completion:default": _digest(completion_authority)},
        tool_grants=tool_grants,
        skill_grants=skill_grants,
        mcp_grants=source_grants,
        allowed_connector_ids=(
            cast(list[str], integration_authority["allowed_connector_ids"])
            if integration_authority is not None
            else []
        ),
        connector_policy_digest=(
            cast(str | None, integration_authority["connector_policy_digest"])
            if integration_authority is not None
            else None
        ),
        allowed_connector_authentication=(
            ["interactive"]
            if integration_authority is not None and integration_authority["allowed_connector_ids"]
            else []
        ),
        allowed_connector_execution_identities=(
            ["auto"]
            if integration_authority is not None and integration_authority["allowed_connector_ids"]
            else []
        ),
        hook_grants=hook_grants,
        sandbox_binding_id="local-workspace",
        sandbox_authority_digest=_digest(cast(JsonValue, {"roots": roots})),
        network_access=True,
        allowed_environment_names=sorted(adapter.env),
        approval_bypass=adapter.bypass_approval,
        max_turn_iterations=config.settings.turn.max_iterations,
        max_output_tokens=adapter.max_tokens,
    )


def _policy_ceiling_digest(policy: ResolvedSubagentPolicyCeiling) -> str:
    # Exclude ambient env var names: the launcher reshuffles them each start and
    # the ceiling never enforces them, so they are launcher noise, not drift.
    return _digest(policy.model_dump(mode="json", exclude={"allowed_environment_names"}))


def _binding_digest(
    config: RustHarnessConfig,
    adapter: LocalRuntimeAdapterConfig,
    *,
    profile: str,
    integration_authority: dict[str, JsonValue] | None,
) -> str:
    return _digest(
        cast(
            JsonValue,
            {
                "profile": profile,
                "core": cast(
                    JsonValue,
                    config.model_dump(mode="json", by_alias=True, exclude_none=False),
                ),
                "adapter": {
                    "provider": adapter.provider,
                    "base_url": adapter.base_url,
                    "model": adapter.model,
                    "temperature": adapter.temperature,
                    "max_tokens": adapter.max_tokens,
                    "timeout_s": adapter.timeout_s,
                    "retry_max_elapsed_time_s": adapter.retry_max_elapsed_time_s,
                    "cwd": str(adapter.cwd.expanduser().resolve()),
                    "workspace_roots": sorted(
                        str(path.expanduser().resolve()) for path in adapter.workspace_roots
                    ),
                    # Ambient env var names omitted; see _policy_ceiling_digest.
                    "bypass_approval": adapter.bypass_approval,
                    "tool_modes": dict(sorted(adapter.tool_modes.items())),
                    "skill_digests": {
                        name: _digest(content) for name, content in sorted(adapter.skills.items())
                    },
                },
                "integration_authority": integration_authority,
            },
        )
    )


def _integration_authority(
    *,
    mcp_catalog: ResolvedMCPCatalog | None,
    connector_catalog: ResolvedConnectorCatalog | None,
    connector_selection: ResolvedConnectorSelection | None,
    connector_gateway_authority_digest: str | None,
) -> dict[str, JsonValue] | None:
    mcp = []
    for server in sorted(
        mcp_catalog.servers if mcp_catalog is not None else (), key=lambda item: item.name
    ):
        authority = cast(
            JsonValue,
            {
                "transport": server.transport,
                "url": server.url,
                "command": server.command,
                "args": list(server.args),
                "cwd": str(server.cwd.expanduser().resolve()) if server.cwd is not None else None,
                "environment_names": sorted(server.env),
                "authorization": {
                    "server_fingerprint": server.authorization.server_fingerprint,
                    "kind": server.authorization.kind,
                    "descriptor_revision": server.authorization.descriptor_revision,
                },
                "prompt_digest": _digest(server.prompt) if server.prompt is not None else None,
                "startup_timeout_s": server.startup_timeout_s,
                "tool_timeout_s": server.tool_timeout_s,
                "sampling_enabled": server.sampling_enabled,
                "disabled": server.disabled,
                "disabled_tools": sorted(server.disabled_tools),
            },
        )
        mcp.append(
            {
                "name": server.name,
                "authority_digest": _digest(authority),
                "allowed_tool_names": [],
            }
        )

    connector_policy_digest: str | None = None
    allowed_connector_ids: list[str] = []
    if connector_catalog is not None or connector_selection is not None:
        if (
            connector_catalog is None
            or connector_selection is None
            or connector_gateway_authority_digest is None
        ):
            raise ValueError("connector authority is not comparable for subagent policy resolution")
        settings = {item.alias: item for item in connector_selection.connector_settings}
        if connector_selection.enable_connectors:
            allowed_connector_ids = sorted(
                connector.raw_id
                for connector in connector_catalog.connectors
                if connector.ready
                and not (
                    (setting := settings.get(connector.alias)) is not None and setting.disabled
                )
            )
        connector_policy_digest = _digest(
            cast(
                JsonValue,
                {
                    "gateway_authority_digest": connector_gateway_authority_digest,
                    "catalog_revision": connector_catalog.revision,
                    "connectors": [
                        {
                            "raw_id": connector.raw_id,
                            "alias": connector.alias,
                            "ready": connector.ready,
                            "auth_action": connector.auth_action,
                            "tools": [
                                {
                                    "raw_name": tool.raw_name,
                                    "input_schema": dict(tool.input_schema),
                                }
                                for tool in connector.tools
                            ],
                        }
                        for connector in sorted(
                            connector_catalog.connectors, key=lambda item: item.raw_id
                        )
                    ],
                    "selection": {
                        "selection_revision": connector_selection.selection_revision,
                        "enable_connectors": connector_selection.enable_connectors,
                        "implicit_source_enabled": connector_selection.implicit_source_enabled,
                        "connector_settings": [
                            {
                                "alias": item.alias,
                                "disabled": item.disabled,
                                "disabled_tools": sorted(item.disabled_tools),
                            }
                            for item in sorted(
                                connector_selection.connector_settings,
                                key=lambda item: item.alias,
                            )
                        ],
                        "enabled_tools": sorted(connector_selection.enabled_tools),
                        "disabled_tools": sorted(connector_selection.disabled_tools),
                    },
                },
            )
        )
    if not mcp and connector_policy_digest is None:
        return None
    return {
        "mcp": cast(JsonValue, mcp),
        "allowed_connector_ids": cast(JsonValue, allowed_connector_ids),
        "connector_policy_digest": connector_policy_digest,
    }


def _digest(value: JsonValue | str) -> str:
    if isinstance(value, str):
        return sha256_json(value)
    return sha256_json(cast(JsonValue, value))


def _permission(
    mode: Literal["allow", "ask", "deny", "classify"],
) -> Literal["never", "ask", "always"]:
    if mode == "allow":
        return "always"
    # `classify` (smart approve) is a gated mode, not a free grant: it caps the
    # subagent's ceiling at ``ask`` like a plain ``ask``.
    if mode in ("ask", "classify"):
        return "ask"
    return "never"


__all__ = [
    "LocalChildSessionBinding",
    "ResolvedSubagentConfiguration",
    "child_config",
    "resolve_declared_agent_types",
    "resolve_subagent_configuration",
]
