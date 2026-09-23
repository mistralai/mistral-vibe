"""Deterministic remote-project responses shared by terminal scenarios."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REPO = "https://github.com/mistralai/vibe.git"
METHODS = frozenset(
    f"vibeCode/projects/{action}"
    for action in ("open", "select", "create", "loadMore", "unlink", "cancel")
)


def project(
    project_id: str, name: str, *, multi: bool = False, read_only: bool = False
) -> dict[str, Any]:
    repositories = [{"repoUrl": REPO, "defaultBranch": "main"}]
    if multi:
        repositories.append({"repoUrl": "https://github.com/mistralai/other"})
    return {
        "projectId": project_id,
        "name": name,
        "repositories": repositories,
        "isReadOnly": read_only,
    }


def view(
    *, saved: bool = True, more: bool = False, empty: bool = False
) -> dict[str, Any]:
    return {
        "context": {
            "repoRoot": "/workspace/vibe",
            "repoUrl": REPO,
            "repoName": "vibe",
            "savedLink": {
                "repoRoot": "/workspace/vibe",
                "repoUrl": REPO,
                "projectId": "linked",
                "projectName": "Vibe team",
            }
            if saved
            else None,
        },
        "state": {
            "repoUrl": REPO,
            "nextCursor": "page-2" if more else None,
            "projects": []
            if empty
            else [
                project("multi", "Shared workspace", multi=True),
                project("exact", "Another project"),
                project("linked", "Vibe team"),
                project("readonly", "Hidden read-only", read_only=True),
            ],
        },
        "git": {
            "remoteName": "origin",
            "remoteUrl": REPO,
            "repo": "mistralai/vibe",
            "branch": "feature",
            "defaultBranch": "main",
        },
    }


def handshake(
    *, saved: bool = True, more: bool = False, empty: bool = False
) -> dict[str, Any]:
    initial = view(saved=saved, more=more, empty=empty)
    loaded = deepcopy(initial)
    loaded["state"]["nextCursor"] = None
    loaded["state"]["projects"].append(project("next", "Next page project"))
    unlinked = deepcopy(initial)
    unlinked["context"]["savedLink"] = None
    return {
        "vibeCode/projects/open": {
            "pickerId": "picker-1",
            "view": initial,
            "resolvedProjectId": None,
        },
        "vibeCode/projects/loadMore": {"view": loaded, "focusOptionId": "project:next"},
        "vibeCode/projects/select": {
            "view": initial,
            "project": project("linked", "Vibe team"),
        },
        "vibeCode/projects/create": {
            "view": initial,
            "project": project("created", "New project"),
        },
        "vibeCode/projects/unlink": {"view": unlinked},
        "vibeCode/projects/cancel": {},
    }
