"""Agent install builders for `/leanstall` and `/unleanstall` scenarios."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import Any

from e2e.app_server.config import FIXTURE_PATH

METHODS = frozenset({"agents/install", "agents/uninstall"})

_runtime = json.loads(FIXTURE_PATH.read_text())["handshake"]["runtime/read"]["runtime"]
FIXTURE_AGENTS: list[dict[str, Any]] = _runtime["agents"]
FIXTURE_ACTIVE: dict[str, Any] = _runtime["activeAgent"]
LEAN = {
    "name": "lean",
    "displayName": "Lean",
    "description": "Specialized mode for Lean 4 code analysis, proof assistance, and theorem proving",
    "safety": "neutral",
    "agentType": "agent",
}
WITH_LEAN = [*FIXTURE_AGENTS, LEAN]


def runtime_read(
    agents: Sequence[Mapping[str, Any]], active: Mapping[str, Any] = FIXTURE_ACTIVE
) -> dict[str, Any]:
    """A `runtime/read` override seeding the session's agents."""
    return {
        "runtime": {"agents": deepcopy(list(agents)), "activeAgent": deepcopy(active)}
    }


def agents_list(
    agents: Sequence[Mapping[str, Any]], active: Mapping[str, Any] = FIXTURE_ACTIVE
) -> dict[str, Any]:
    """An `AgentsListResponse`, the answer to `agents/install` and `agents/uninstall`."""
    return {"active": deepcopy(active), "agents": deepcopy(list(agents))}
