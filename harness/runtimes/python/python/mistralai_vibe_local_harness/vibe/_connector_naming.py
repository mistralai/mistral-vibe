"""Deterministic Core identities for Runtime-owned connector tools."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from fnmatch import fnmatch
import functools
import hashlib
import json
import re
from types import MappingProxyType

from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorRouteSnapshot,
    ConnectorSourceState,
    ConnectorSourceStatus,
    ConnectorToolDescriptor,
    ConnectorToolGroup,
    ConnectorToolRoute,
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
)

NAMING_VERSION = "mistral.vibe.connector-naming/v1"
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_IDENTIFIER_CHARACTER = re.compile(r"[A-Za-z0-9_$]")


def build_connector_snapshot(
    catalog: ResolvedConnectorCatalog,
    selection: ResolvedConnectorSelection,
    *,
    source_overrides: Mapping[str, str] | None = None,
    suspended_tools: Iterable[tuple[str, str]] = (),
    claimed_groups: Iterable[str] = (),
) -> ConnectorRouteSnapshot:
    overrides = dict(source_overrides or {})
    suspended = set(suspended_tools)
    connectors = sorted(catalog.connectors, key=lambda item: (item.alias, item.raw_id))
    group_names = _group_names(connectors, set(claimed_groups))
    groups: list[ConnectorToolGroup] = []
    route_inputs: list[tuple[str, str, str, str]] = []
    sources: list[ConnectorSourceState] = []
    descriptors_by_alias: dict[str, tuple[ConnectorToolDescriptor, ...]] = {}

    for connector in connectors:
        source_enabled = connector_source_enabled(selection, connector.alias)
        status = _source_status(
            connector, source_enabled, overrides.get(connector.alias)
        )
        tool_names = _tool_names([tool.raw_name for tool in connector.tools])
        descriptors = tuple(
            ConnectorToolDescriptor(
                raw_connector_id=connector.raw_id,
                alias=connector.alias,
                remote_name=tool.raw_name,
                group_name=group_names[connector.raw_id],
                programmatic_name=tool_names[tool.raw_name],
                display_name=_normalize_identifier(
                    f"connector_{connector.alias}_{tool.raw_name}"
                ),
                description=tool.description or f"Connector tool {tool.raw_name}.",
                input_schema=dict(tool.input_schema),
                enabled=(
                    status == "connected"
                    and connector_tool_enabled(
                        selection, alias=connector.alias, raw_tool_name=tool.raw_name
                    )
                    and (connector.alias, tool.raw_name) not in suspended
                ),
            )
            for tool in sorted(connector.tools, key=lambda item: item.raw_name)
        )
        descriptors_by_alias[connector.alias] = descriptors
        enabled_tools = tuple(tool for tool in descriptors if tool.enabled)
        if enabled_tools:
            groups.append(
                ConnectorToolGroup(
                    name=group_names[connector.raw_id],
                    description=f"Tools provided by connector {connector.display_name}.",
                    tools=enabled_tools,
                )
            )
            route_inputs.extend(
                (
                    descriptor.group_name,
                    descriptor.programmatic_name,
                    connector.raw_id,
                    descriptor.remote_name,
                )
                for descriptor in enabled_tools
            )
        sources.append(
            ConnectorSourceState(
                raw_id=connector.raw_id,
                alias=connector.alias,
                display_name=connector.display_name,
                status=status,
                tools=descriptors,
                error="; ".join(connector.diagnostics) or None,
            )
        )

    route_revision = _route_revision(
        catalog.revision, groups, route_inputs, sources=sources
    )
    routes = {
        (group_name, tool_name): ConnectorToolRoute(
            group_name=group_name,
            tool_name=tool_name,
            raw_connector_id=raw_connector_id,
            remote_tool_name=remote_tool_name,
            catalog_revision=catalog.revision,
            route_revision=route_revision,
        )
        for group_name, tool_name, raw_connector_id, remote_tool_name in route_inputs
    }
    return ConnectorRouteSnapshot(
        catalog_revision=catalog.revision,
        selection_revision=selection.selection_revision,
        route_revision=route_revision,
        groups=tuple(groups),
        routes=MappingProxyType(routes),
        sources=tuple(sources),
    )


def connector_source_enabled(selection: ResolvedConnectorSelection, alias: str) -> bool:
    if not selection.enable_connectors:
        return False
    setting = next(
        (item for item in selection.connector_settings if item.alias == alias), None
    )
    return (
        selection.implicit_source_enabled if setting is None else not setting.disabled
    )


def connector_tool_enabled(
    selection: ResolvedConnectorSelection, *, alias: str, raw_tool_name: str
) -> bool:
    if not connector_source_enabled(selection, alias):
        return False
    setting = next(
        (item for item in selection.connector_settings if item.alias == alias), None
    )
    if setting is not None and raw_tool_name in setting.disabled_tools:
        return False
    published_name = f"connector_{alias}_{raw_tool_name}"
    if selection.enabled_tools and not _name_matches(
        published_name, selection.enabled_tools
    ):
        return False
    return not (
        selection.disabled_tools
        and _name_matches(published_name, selection.disabled_tools)
    )


def _source_status(  # noqa: PLR0911 - one return per source status
    connector: ResolvedConnector, source_enabled: bool, override: str | None
) -> ConnectorSourceStatus:
    if not source_enabled:
        return "disabled"
    if override == "disabled":
        return "disabled"
    if override == "needs_auth":
        return "needs_auth"
    if override == "needs_setup":
        return "needs_setup"
    if override == "unavailable":
        return "unavailable"
    if connector.ready:
        return "connected"
    if connector.auth_action == "oauth":
        return "needs_auth"
    if connector.auth_action == "credentials_setup":
        return "needs_setup"
    return "unavailable"


def _group_names(
    connectors: list[ResolvedConnector], claimed: set[str]
) -> dict[str, str]:
    candidates = {
        connector.raw_id: f"connector_{_normalize_segment(connector.alias)}"
        for connector in connectors
    }
    owners: dict[str, list[str]] = defaultdict(list)
    for raw_id, candidate in candidates.items():
        owners[candidate].append(raw_id)
    result: dict[str, str] = {}
    used = set(claimed)
    for connector in connectors:
        candidate = candidates[connector.raw_id]
        if (
            candidate not in claimed
            and len(owners[candidate]) == 1
            and candidate not in used
        ):
            result[connector.raw_id] = candidate
            used.add(candidate)
            continue
        digest = hashlib.sha256(
            b"connector\0" + connector.raw_id.encode("utf-8")
        ).hexdigest()
        result[connector.raw_id] = _unique_digest_name(
            candidate, digest, used | claimed
        )
        used.add(result[connector.raw_id])
    return result


def _tool_names(raw_names: list[str]) -> dict[str, str]:
    return _resolve_normalized_names(
        sorted(set(raw_names)),
        normalize=_normalize_identifier,
        digest=lambda raw: hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


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
        if _IDENTIFIER.fullmatch(raw) is not None and raw == candidate:
            result[raw] = raw
            used.add(raw)
            continue
        if len(owners[candidate]) == 1 and candidate not in used:
            result[raw] = candidate
            used.add(candidate)
            continue
        result[raw] = _unique_digest_name(candidate, digest(raw), used)
        used.add(result[raw])
    return result


def _normalize_segment(value: str) -> str:
    normalized = "".join(
        character if _IDENTIFIER_CHARACTER.fullmatch(character) else "_"
        for character in value
    )
    return normalized or "_"


def _normalize_identifier(value: str) -> str:
    normalized = _normalize_segment(value)
    return f"_{normalized}" if normalized[0].isdigit() else normalized


def _unique_digest_name(candidate: str, digest: str, used: set[str]) -> str:
    length = 8
    while True:
        name = f"{candidate}_{digest[:length]}"
        if name not in used:
            return name
        if length >= len(digest):
            raise ValueError("Unable to resolve deterministic connector name collision")
        length += 1


@functools.lru_cache(maxsize=256)
def _compile_icase(expression: str) -> re.Pattern[str] | None:
    try:
        return re.compile(expression, re.IGNORECASE)
    except re.error:
        return None


def _name_matches(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    for raw in patterns:
        pattern = raw.strip()
        if not pattern:
            continue
        if pattern.startswith("re:"):
            regex = _compile_icase(pattern.removeprefix("re:"))
            if regex is not None and regex.fullmatch(name) is not None:
                return True
        elif fnmatch(lowered, pattern.lower()):
            return True
    return False


def _route_revision(
    catalog_revision: str,
    groups: list[ConnectorToolGroup],
    routes: list[tuple[str, str, str, str]],
    *,
    sources: list[ConnectorSourceState],
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
                        "connector": tool.raw_connector_id,
                        "remote": tool.remote_name,
                        "display": tool.display_name,
                        "schema": tool.input_schema,
                    }
                    for tool in group.tools
                ],
            }
            for group in groups
        ],
        "routes": sorted("\0".join(route) for route in routes),
        "sources": [
            {"rawId": source.raw_id, "alias": source.alias, "status": source.status}
            for source in sources
        ],
    }
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "NAMING_VERSION",
    "build_connector_snapshot",
    "connector_source_enabled",
    "connector_tool_enabled",
]
