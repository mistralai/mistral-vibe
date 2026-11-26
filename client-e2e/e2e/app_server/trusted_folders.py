"""Trust-store answers for `/trusted-folders` scenarios."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

METHODS = frozenset({
    "workspace/trust/folders/add",
    "workspace/trust/folders/inspect",
    "workspace/trust/folders/list",
    "workspace/trust/folders/update",
})


def folders(trusted: Sequence[str], untrusted: Sequence[str]) -> dict[str, Any]:
    """A `workspace/trust/folders/*` answer; paths sit outside any home directory."""
    return {
        "trusted": list(trusted),
        "untrusted": list(untrusted),
        "settingsPath": "/home/user/.vibe/trusted_folders.toml",
    }


def candidate(
    path: str,
    *,
    problem: str | None = None,
    stored: str | None = None,
    untrusted_inside: Sequence[str] = (),
) -> dict[str, Any]:
    """One folder a `workspace/trust/folders/inspect` answer offers."""
    return {
        "path": path,
        "value": f"{path}/",
        "problem": problem,
        "stored": stored,
        "trustedBy": None,
        "untrustedInside": list(untrusted_inside),
        "untrustedInsideCount": len(untrusted_inside),
    }


def inspection(
    *candidates: dict[str, Any], typed_problem: str | None = None
) -> dict[str, Any]:
    """A `workspace/trust/folders/inspect` answer: the typed folder first, then completions."""
    return {"typedProblem": typed_problem, "candidates": list(candidates)}
