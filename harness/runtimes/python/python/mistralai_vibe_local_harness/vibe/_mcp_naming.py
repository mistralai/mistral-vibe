"""Deterministic Core identities for Runtime-owned MCP descriptors."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
import hashlib
import json
import re
from types import MappingProxyType

from mistralai_vibe_local_harness.vibe._mcp_apps import (
    APP_AUDIENCE,
    MODEL_AUDIENCE,
    mcp_tool_visibility,
)
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPRemoteToolDescriptor,
    MCPRouteSnapshot,
    MCPSourceState,
    MCPToolDescriptor,
    MCPToolFilter,
    MCPToolGroup,
    MCPToolRoute,
    ResolvedMCPServerConfig,
)
from mistralai_vibe_local_harness.vibe._tool_schemas import type_is_not_object

NAMING_VERSION = "mistral.vibe.mcp-naming/v1"
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_IDENTIFIER_CHARACTER = re.compile(r"[A-Za-z0-9_$]")


def build_route_snapshot(
    *,
    catalog_revision: str,
    resolved: Iterable[
        tuple[ResolvedMCPServerConfig, tuple[MCPRemoteToolDescriptor, ...]]
    ],
    sources: tuple[MCPSourceState, ...],
    claimed_groups: Iterable[str] = (),
    tool_filter: MCPToolFilter | None = None,
) -> MCPRouteSnapshot:
    entries = sorted(resolved, key=lambda item: item[0].name)
    group_names = _group_names([server for server, _ in entries], set(claimed_groups))
    programmatic_names = {
        server.name: _tool_names(descriptors) for server, descriptors in entries
    }
    display_names = _display_names(entries)
    groups: list[MCPToolGroup] = []
    all_tools: list[MCPToolDescriptor] = []
    routes: dict[tuple[str, str], MCPToolRoute] = {}
    for server, descriptors in entries:
        group_name = group_names[server.name]
        tools: list[MCPToolDescriptor] = []
        for remote in sorted(
            descriptors, key=lambda descriptor: descriptor.remote_name
        ):
            descriptor = _tool_descriptor(
                server,
                remote,
                group_name=group_name,
                programmatic_name=programmatic_names[server.name][remote.remote_name],
                display_name=display_names[(server.name, remote.remote_name)],
                tool_filter=tool_filter,
            )
            tools.append(descriptor)
            all_tools.append(descriptor)
            if descriptor.enabled:
                routes[group_name, descriptor.programmatic_name] = MCPToolRoute(
                    group_name=group_name,
                    tool_name=descriptor.programmatic_name,
                    descriptor=descriptor,
                    server_fingerprint=server.authorization.server_fingerprint,
                )
        enabled_tools = tuple(tool for tool in tools if tool.enabled)
        if enabled_tools:
            groups.append(
                MCPToolGroup(
                    name=group_name,
                    description=server.prompt
                    or f"Tools provided by MCP server {server.name}.",
                    tools=enabled_tools,
                )
            )
    route_revision = _route_revision(catalog_revision, groups, routes)
    return MCPRouteSnapshot(
        catalog_revision=catalog_revision,
        route_revision=route_revision,
        groups=tuple(groups),
        routes=MappingProxyType(routes),
        sources=sources,
        tools=tuple(all_tools),
    )


def mcp_tool_config_name(server_name: str, remote_name: str) -> str:
    """The name Vibe config and permission rules give a server's tool.

    A tool's display name is this one unless it is not an identifier or two
    tools share it.
    """
    return f"{server_name}_{remote_name}"


def _tool_descriptor(
    server: ResolvedMCPServerConfig,
    remote: MCPRemoteToolDescriptor,
    *,
    group_name: str,
    programmatic_name: str,
    display_name: str,
    tool_filter: MCPToolFilter | None,
) -> MCPToolDescriptor:
    user_disabled = _user_disabled(
        server, remote.remote_name, display_name, tool_filter
    )
    visibility = mcp_tool_visibility(remote.meta)
    return MCPToolDescriptor(
        server_name=server.name,
        remote_name=remote.remote_name,
        group_name=group_name,
        programmatic_name=programmatic_name,
        display_name=display_name,
        description=remote.description,
        input_schema=remote.input_schema,
        output_schema=remote.output_schema,
        annotations=remote.annotations,
        # Core refuses a tool whose schema's type is not "object": only that
        # tool is left out, and the server's other tools stay routed.
        enabled=not user_disabled
        and not type_is_not_object(remote.input_schema)
        and MODEL_AUDIENCE in visibility,
        meta=remote.meta,
        user_disabled=user_disabled,
        model_visible=MODEL_AUDIENCE in visibility,
        app_visible=APP_AUDIENCE in visibility,
    )


def _user_disabled(
    server: ResolvedMCPServerConfig,
    remote_name: str,
    display_name: str,
    tool_filter: MCPToolFilter | None,
) -> bool:
    """Whether the user's ``disabled_tools`` or the Host filter leaves the tool out."""
    if remote_name in server.disabled_tools:
        return True
    return tool_filter is not None and not tool_filter.allows(display_name)


def _group_names(
    servers: list[ResolvedMCPServerConfig], claimed: set[str]
) -> dict[str, str]:
    candidates: dict[str, str] = {
        server.name: f"mcp_{_normalize_segment(server.name)}" for server in servers
    }
    owners: dict[str, list[str]] = defaultdict(list)
    for raw, candidate in candidates.items():
        owners[candidate].append(raw)
    result: dict[str, str] = {}
    used = set(claimed)
    for server in sorted(servers, key=lambda item: item.name):
        candidate = candidates[server.name]
        if (
            candidate not in claimed
            and len(owners[candidate]) == 1
            and candidate not in used
        ):
            result[server.name] = candidate
            used.add(candidate)
            continue
        digest = hashlib.sha256(
            b"configured\0" + server.name.encode("utf-8")
        ).hexdigest()
        suffixed = _unique_digest_name(candidate, digest, used | claimed)
        result[server.name] = suffixed
        used.add(suffixed)
    return result


def _tool_names(descriptors: tuple[MCPRemoteToolDescriptor, ...]) -> dict[str, str]:
    raw_names = sorted({descriptor.remote_name for descriptor in descriptors})
    return _resolve_normalized_names(
        raw_names,
        normalize=_normalize_identifier,
        digest=lambda raw: hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def _display_names(
    entries: list[tuple[ResolvedMCPServerConfig, tuple[MCPRemoteToolDescriptor, ...]]],
) -> dict[tuple[str, str], str]:
    identities = [
        (server.name, descriptor.remote_name)
        for server, descriptors in entries
        for descriptor in descriptors
    ]
    candidates = {
        identity: _normalize_identifier(mcp_tool_config_name(*identity))
        for identity in identities
    }
    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for identity, candidate in candidates.items():
        owners[candidate].append(identity)
    result: dict[tuple[str, str], str] = {}
    used: set[str] = set()
    for identity in sorted(identities):
        candidate = candidates[identity]
        raw_candidate = mcp_tool_config_name(*identity)
        if _is_identifier(raw_candidate) and len(owners[candidate]) == 1:
            result[identity] = raw_candidate
            used.add(raw_candidate)
            continue
        if len(owners[candidate]) == 1 and candidate not in used:
            result[identity] = candidate
            used.add(candidate)
            continue
        digest = hashlib.sha256(
            identity[0].encode("utf-8") + b"\0" + identity[1].encode("utf-8")
        ).hexdigest()
        result[identity] = _unique_digest_name(candidate, digest, used)
        used.add(result[identity])
    return result


def _resolve_normalized_names(
    raw_names: list[str],
    *,
    normalize: Callable[[str], str],
    digest: Callable[[str], str],
) -> dict[str, str]:
    candidates = {raw: normalize(raw) for raw in raw_names}
    owners: dict[str, list[str]] = defaultdict(list)
    for raw, candidate in candidates.items():
        owners[candidate].append(raw)
    result: dict[str, str] = {}
    used: set[str] = set()
    for raw in raw_names:
        candidate = candidates[raw]
        exact_owner = raw if _is_identifier(raw) and raw == candidate else None
        if exact_owner is not None:
            result[raw] = raw
            used.add(raw)
            continue
        if len(owners[candidate]) == 1 and candidate not in used:
            result[raw] = candidate
            used.add(candidate)
            continue
        resolved = _unique_digest_name(candidate, digest(raw), used)
        result[raw] = resolved
        used.add(resolved)
    return result


def _normalize_segment(value: str) -> str:
    normalized = "".join(
        character if _IDENTIFIER_CHARACTER.fullmatch(character) else "_"
        for character in value
    )
    return normalized or "_"


def _normalize_identifier(value: str) -> str:
    normalized = _normalize_segment(value)
    if normalized[0].isdigit():
        normalized = f"_{normalized}"
    return normalized


def _is_identifier(value: str) -> bool:
    return _IDENTIFIER.fullmatch(value) is not None


def _unique_digest_name(candidate: str, digest: str, used: set[str]) -> str:
    length = 8
    while True:
        name = f"{candidate}_{digest[:length]}"
        if name not in used:
            return name
        if length >= len(digest):
            raise ValueError("Unable to resolve deterministic MCP name collision")
        length += 1


def _route_revision(
    catalog_revision: str,
    groups: list[MCPToolGroup],
    routes: dict[tuple[str, str], MCPToolRoute],
) -> str:
    value = {
        "namingVersion": NAMING_VERSION,
        "catalogRevision": catalog_revision,
        "groups": [
            {
                "name": group.name,
                "tools": [
                    {
                        "name": tool.programmatic_name,
                        "server": tool.server_name,
                        "remote": tool.remote_name,
                        "display": tool.display_name,
                        "schema": tool.input_schema,
                    }
                    for tool in group.tools
                ],
            }
            for group in groups
        ],
        "routes": sorted(f"{group}\0{tool}" for group, tool in routes),
    }
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["NAMING_VERSION", "build_route_snapshot"]
