"""Skill rows in the `/` menu lead their description with their scope, whatever their source; built-ins do not."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline


def _skill(name: str, description: str, **origin: str) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "prompt": "",
        "userInvocable": True,
        **origin,
    }


_SKILLS = [
    _skill("changelog", "Draft the changelog entry.", source="local", scope="global"),
    _skill("ci-status", "Summarize the CI run.", source="local", scope="project"),
    _skill("cloud:deploy", "Deploy to the cloud.", source="plugin", scope="project"),
    _skill("code-style", "Apply the team style.", source="registry", scope="global"),
    _skill("commit-msg", "Write a commit message.", source="builtin", scope="builtin"),
]

handshake = {"runtime/read": {"runtime": {"skills": _SKILLS}}}

capture_startup = False
screen_contains = (
    "user · Draft the changelog entry.",
    "project · Summarize the CI run.",
    "project · Deploy to the cloud.",
    "user · Apply the team style.",
    "vibe · Write a commit message.",
)

timeline: Timeline = ["/c"]
