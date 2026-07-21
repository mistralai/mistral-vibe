from __future__ import annotations

import argparse
from collections.abc import Callable

from vibe.cli.cli import get_initial_agent_name
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema


def _make_args(
    *, agent: str | None, prompt: str | None, auto_approve: bool = False
) -> argparse.Namespace:
    return argparse.Namespace(agent=agent, prompt=prompt, auto_approve=auto_approve)


def test_uses_args_agent_when_provided(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent="accept-edits", prompt=None)

    assert get_initial_agent_name(args, config) == "accept-edits"


def test_falls_back_to_config_default_agent_when_args_agent_is_none(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent=None, prompt=None)

    assert get_initial_agent_name(args, config) == BuiltinAgentName.PLAN


def test_defaults_to_default_when_unset_in_config_and_args(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config()
    args = _make_args(agent=None, prompt=None)

    assert get_initial_agent_name(args, config) == BuiltinAgentName.DEFAULT


def test_programmatic_mode_falls_back_to_default_agent(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.DEFAULT)
    args = _make_args(agent=None, prompt="hello")

    assert get_initial_agent_name(args, config) == BuiltinAgentName.DEFAULT


def test_programmatic_mode_uses_config_default_agent_when_args_agent_is_none(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent=None, prompt="hello")

    assert get_initial_agent_name(args, config) == BuiltinAgentName.PLAN


def test_programmatic_mode_keeps_explicit_agent_arg(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.DEFAULT)
    args = _make_args(agent="accept-edits", prompt="hello")

    assert get_initial_agent_name(args, config) == "accept-edits"


def test_auto_approve_flag_keeps_config_default_agent(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent=None, prompt="hello", auto_approve=True)

    assert get_initial_agent_name(args, config) == BuiltinAgentName.PLAN


def test_auto_approve_flag_keeps_explicit_agent_arg(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(default_agent=BuiltinAgentName.PLAN)
    args = _make_args(agent="lean", prompt="hello", auto_approve=True)

    assert get_initial_agent_name(args, config) == BuiltinAgentName.LEAN
