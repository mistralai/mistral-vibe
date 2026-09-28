"""Build temporary replay-server fixtures for Vibe scenarios."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import turn_queue_updated
from e2e.app_server.scenario import Scenario

_CONNECTOR_REVISION = "connector-revision-1"

# A disabled connector is a selection, not a catalog readiness state.
_READINESS = {
    "connected": "ready",
    "disabled": "ready",
    "needs_auth": "needs_auth",
    "needs_setup": "needs_setup",
    "unavailable": "unavailable",
}


@contextmanager
def replay_fixture(scenario: Scenario) -> Iterator[str]:
    """Yield a temporary replay fixture and remove it afterward."""
    responses = json.loads(FIXTURE_PATH.read_text())["handshake"]
    _deep_merge(responses, scenario.handshake)
    _derive_runtime_responses(responses, scenario.handshake)
    descriptor, path = tempfile.mkstemp(prefix="e2e_replay_", suffix=".json")
    try:
        with os.fdopen(descriptor, "w") as fixture:
            json.dump(
                {
                    "handshake": responses,
                    "events": [
                        _drain_queue_on_turn_start(batch)
                        for batch in scenario.event_batches()
                    ],
                    "onRequest": scenario.on_request,
                    "declared": _declared(scenario.handshake),
                    "catalogs": _catalogs(responses),
                },
                fixture,
            )
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def _declared(handshake: Mapping[str, Any]) -> list[str]:
    """Methods the scenario pinned; they answer as written and move the server on."""
    # `runtime/read` is excluded: it is the seed holding the server's live runtime.
    declared = {method for method in handshake if method != "runtime/read"}
    aliases = {
        method.replace("mcp/", "mcp_catalog/", 1)
        for method in declared
        if method.startswith("mcp/")
    }
    return sorted(declared | aliases)


def _catalogs(responses: Mapping[str, Any]) -> dict[str, Any]:
    """The connector catalog each runtime-carrying response implies, by method."""
    # The catalog is a projection of the runtime, so adopting one adopts both.
    return {
        method: _connector_catalog(response["runtime"])
        for method, response in responses.items()
        if isinstance(response, dict) and isinstance(response.get("runtime"), dict)
    }


def _drain_queue_on_turn_start(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Follow each started turn with the queue update that retires its item."""
    drained: list[dict[str, Any]] = []
    for event in batch:
        drained.append(event)
        if event.get("method") == "turn/started":
            drain = turn_queue_updated()
            # The drain belongs to the turn's session, which a clear may replace.
            drain["params"]["sessionId"] = event["params"]["sessionId"]
            drained.append(drain)
    return drained


def _connector_catalog(runtime: dict[str, Any]) -> dict[str, Any]:
    """Project the runtime's connector sources as a connector-catalog read."""
    connectors = [
        source
        for source in runtime.get("mcp", {}).get("sources", [])
        if source.get("kind") == "connector"
    ]
    return {
        "catalog": {
            "disposition": "memory",
            "catalogRevision": _CONNECTOR_REVISION,
            "connectors": [
                {
                    "alias": source["name"],
                    "displayName": source["name"],
                    "readiness": _READINESS.get(source.get("status", ""), "ready"),
                    "authAction": "oauth"
                    if source.get("status") == "needs_auth"
                    else "none",
                    "tools": [
                        {"name": tool["name"], "description": tool.get("description")}
                        for tool in source.get("tools", [])
                    ],
                    "diagnostic": source.get("error"),
                }
                for source in connectors
            ],
        },
        "selections": [
            {
                "alias": source["name"],
                "disabled": source.get("status") == "disabled",
                "disabledTools": [
                    tool["name"]
                    for tool in source.get("tools", [])
                    if not tool.get("enabled", True)
                ],
                "state": "resolved",
            }
            for source in connectors
        ],
        "session": {
            "acceptedCatalogRevision": _CONNECTOR_REVISION,
            "acceptedSelectionRevision": _CONNECTOR_REVISION,
            "routeRevision": _CONNECTOR_REVISION,
            "sources": [
                {
                    "alias": source["name"],
                    "displayName": source["name"],
                    "status": source.get("status", "connected"),
                    "tools": source.get("tools", []),
                    "error": source.get("error"),
                }
                for source in connectors
            ],
        },
    }


def _derive_runtime_responses(
    responses: dict[str, Any], overrides: Mapping[str, Any]
) -> None:
    """Answer every runtime-mutating method from the session's runtime snapshot."""
    runtime = responses["runtime/read"]["runtime"]
    derived: dict[str, Any] = {
        "config/reload": {"runtime": runtime, "strippedHistoryImages": 0},
        "session/compact": {
            "summary": "Compacted summary.",
            "state": deepcopy(responses["session/read"]["state"]),
            "sessionLog": {"enabled": False},
        },
        # Connectors reach the browser through their own catalog now, so mirror
        # the runtime's connector sources into the session view.
        "connector_catalog/read": _connector_catalog(runtime),
        "mcp/read": {"mcp": deepcopy(runtime["mcp"])},
        # Connector auth: no URL and no new tools unless the scenario says so.
        "connectors/auth/read": {"url": None},
        "connectors/refresh": {"toolCount": 0, "runtime": deepcopy(runtime)},
        "mcp/add": {
            "name": "example",
            "url": "https://example.invalid/mcp",
            "created": True,
            "runtime": deepcopy(runtime),
        },
        **{
            method: {"runtime": deepcopy(runtime)}
            for method in (
                "mcp/refresh",
                "mcp/toggle",
                "mcp/login",
                "mcp/logout",
                "connector_catalog/refresh",
                "connector_catalog/toggle",
            )
        },
    }
    for method, response in derived.items():
        _deep_merge(response, overrides.get(method, {}))
        responses[method] = response
        # The engine renamed the MCP surface; answer both names so scenario
        # handshakes stay keyed on the short one.
        if method.startswith("mcp/"):
            responses[method.replace("mcp/", "mcp_catalog/", 1)] = deepcopy(response)


def _deep_merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
