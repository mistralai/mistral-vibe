"""Skills browser builders for scenarios: pins, the shared catalog, versions and bodies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

METHODS = frozenset({
    "skills/installed",
    "skills/catalog",
    "skills/versions",
    "skills/detail",
    "skills/updates",
    "skills/import",
    "skills/remove",
    "skills/setEnabled",
})

APPLIED = {"status": "applied"}


def registry_on() -> dict[str, Any]:
    """Registry skills on, with no update to announce at startup."""
    config = {"experimentalEnableRegistrySkills": True}
    return {
        "runtime/read": {"runtime": {"config": config}},
        "skills/updates": {"updates": []},
    }


def skill(
    name: str,
    *,
    source: str = "local",
    scope: str = "global",
    version: int | None = None,
    alias: str | None = None,
    enabled: bool = True,
    locked: bool = False,
    active: bool = True,
    description: str = "",
    prompt: str | None = None,
) -> dict[str, Any]:
    """One installed pin; a `version` gives it a registry ref, and `active=False` shadows it."""
    return {
        "name": name,
        "description": description,
        "prompt": f"# {name}\n\nUse {name} when the task calls for it."
        if prompt is None
        else prompt,
        "userInvocable": True,
        "source": source,
        "scope": scope,
        "registry": None
        if version is None
        else {"skillId": f"skill-{name}", "version": version, "alias": alias},
        "enabled": enabled,
        "locked": locked,
        "active": active,
    }


def of_skill(pin: Mapping[str, Any], skill_id: str) -> dict[str, Any]:
    """`pin` as a pin of the shared skill `skill_id`, as when two names pin one stored version."""
    return {**pin, "registry": {**pin["registry"], "skillId": skill_id}}


def installed(skills: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A `skills/installed` answer."""
    return {"skills": list(skills)}


def entry(
    name: str, latest: int = 1, sharing: str = "workspace", description: str = ""
) -> dict[str, Any]:
    """One shared skill in the catalog."""
    return {
        "name": name,
        "skillId": f"skill-{name}",
        "description": description,
        "latestVersion": latest,
        "sharingScope": sharing,
    }


def catalog(
    entries: Sequence[Mapping[str, Any]] = (),
    *,
    updates: Mapping[str, int] | None = None,
    loaded: bool = True,
    project: bool = True,
    authenticated: bool = True,
) -> dict[str, Any]:
    """A `skills/catalog` answer."""
    return {
        "skills": list(entries),
        "updates": dict(updates or {}),
        "loaded": loaded,
        "projectAvailable": project,
        "authenticated": authenticated,
    }


def versions(
    *numbers: int, aliases: Mapping[int, Sequence[str]] | None = None
) -> dict[str, Any]:
    """A `skills/versions` answer, newest first."""
    named = aliases or {}
    return {
        "versions": [
            {"version": number, "aliases": list(named.get(number, ()))}
            for number in sorted(numbers, reverse=True)
        ]
    }


def detail(body: str) -> dict[str, Any]:
    """A `skills/detail` answer carrying `body`."""
    return {"detail": None, "body": body}


def long_body_handshake() -> dict[str, Any]:
    """A registry pin whose body is longer than any pane, with what its page reads."""
    steps = [f"Step {step}: check the release gate." for step in range(1, 61)]
    body = "\n".join(["# deploy", "", *steps])
    return {
        **registry_on(),
        "skills/installed": installed([
            skill("deploy", source="registry", version=2, prompt=body)
        ]),
        "skills/catalog": catalog(),
        "skills/versions": versions(2, 1),
    }


def sample_skills() -> list[dict[str, Any]]:
    """Pins in server order: both scopes, every source, a disabled and two locked rows, and a shadowed pin."""
    return [
        skill("code-review", description="Review a diff for bugs"),
        skill("lint", scope="project", enabled=False),
        skill("experimental-search", enabled=False, locked=True),
        skill("deploy", source="registry", scope="project", version=3, alias="latest"),
        skill("release-notes", source="registry", version=2),
        skill("pdf", source="plugin", locked=True),
        skill("deploy", source="registry", version=1, active=False),
    ]


def sample_catalog() -> dict[str, Any]:
    """Shared skills to import, and a newer version of the frozen `release-notes` pin."""
    return catalog(
        [
            entry("translate", 4, "workspace", "Translate docs"),
            entry("changelog", 2, "private"),
            entry("deploy", 3),
        ],
        updates={"release-notes": 5},
    )
