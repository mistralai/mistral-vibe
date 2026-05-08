from __future__ import annotations

import argparse

from vibe.cli.cli import get_initial_agent_name
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfig


def _make_args(*, agent: str | None, prompt: str | None) -> argparse.Namespace:
    return argparse.Namespace(agent=agent, prompt=prompt)


def test_uses_args_agent_when_provided() -> None:
    config = VibeConfig.model_construct(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent="accept-edits", prompt=None)

    assert get_initial_agent_name(args, config) == "accept-edits"


def test_falls_back_to_config_default_agent_when_args_agent_is_none() -> None:
    config = VibeConfig.model_construct(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent=None, prompt=None)

    assert get_initial_agent_name(args, config) == BuiltinAgentName.PLAN


def test_defaults_to_default_when_unset_in_config_and_args() -> None:
    config = VibeConfig.model_construct()
    args = _make_args(agent=None, prompt=None)

    assert get_initial_agent_name(args, config) == BuiltinAgentName.DEFAULT


def test_programmatic_mode_promotes_default_to_auto_approve() -> None:
    config = VibeConfig.model_construct(default_agent=BuiltinAgentName.DEFAULT)
    args = _make_args(agent=None, prompt="hello")

    assert get_initial_agent_name(args, config) == BuiltinAgentName.AUTO_APPROVE


def test_programmatic_mode_ignores_config_default_agent_when_args_agent_is_none() -> (
    None
):
    config = VibeConfig.model_construct(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent=None, prompt="hello")

    assert get_initial_agent_name(args, config) == BuiltinAgentName.AUTO_APPROVE


def test_programmatic_mode_keeps_explicit_agent_arg() -> None:
    config = VibeConfig.model_construct(default_agent=BuiltinAgentName.DEFAULT)
    args = _make_args(agent="accept-edits", prompt="hello")

    assert get_initial_agent_name(args, config) == "accept-edits"
