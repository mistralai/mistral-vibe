from __future__ import annotations

import pytest

from vibe.app_server.models import AgentSafety, AgentSummary, AgentType
from vibe.cli.textual_ui.app import _indicator_agent_name, _indicator_safety


def _agent(name: str, safety: AgentSafety) -> AgentSummary:
    return AgentSummary(
        name=name,
        display_name=name.replace("-", " ").title(),
        description="",
        safety=safety,
        agent_type=AgentType.AGENT,
    )


@pytest.mark.parametrize(
    ("agent", "safety", "expected"),
    [
        ("plan", AgentSafety.SAFE, "plan · auto-approve"),
        ("accept-edits", AgentSafety.DESTRUCTIVE, "accept edits · auto-approve"),
        # A custom profile declares its own ``safety``; nothing stops it from
        # claiming SAFE while its overrides bypass approval.
        ("audit", AgentSafety.SAFE, "audit · auto-approve"),
        # The profile already carries the word, so repeating it reads as a bug.
        ("auto-approve", AgentSafety.YOLO, "auto approve"),
    ],
)
def test_a_bypass_the_profile_does_not_imply_is_named_in_the_indicator(
    agent: str, safety: AgentSafety, expected: str
) -> None:
    """The indicator must not under-report what the session will run.

    ``--auto-approve`` and a config-level ``bypass_tool_permissions`` both
    approve every tool call without changing the agent, so labelling the
    indicator from the profile alone advertises a mode the session ignores.
    """
    profile = _agent(agent, safety)

    assert _indicator_agent_name(profile, True) == expected
    assert _indicator_safety(profile, True) is AgentSafety.YOLO


@pytest.mark.parametrize(
    ("agent", "safety"),
    [
        ("ask", AgentSafety.NEUTRAL),
        ("plan", AgentSafety.SAFE),
        ("auto-approve", AgentSafety.YOLO),
    ],
)
def test_without_a_bypass_the_indicator_is_just_the_agent(
    agent: str, safety: AgentSafety
) -> None:
    profile = _agent(agent, safety)

    assert _indicator_agent_name(profile, False) == profile.display_name.lower()
    assert _indicator_safety(profile, False) is safety
