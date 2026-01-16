from __future__ import annotations

import argparse
import sys

from vibe.cli.cli import bootstrap_config_files, get_initial_mode, load_session
from vibe.cli.textual_ui.app import run_textual_ui
from vibe.core.config import MissingAPIKeyError, VibeConfig, load_api_keys_from_env
from vibe.core.paths.config_paths import unlock_config_paths


def parse_child_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Vibe Textual UI (spawned by textual-serve)",
    )
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help="Load agent configuration from ~/.vibe/agents/NAME.toml",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=False,
        help="Start in auto-approve mode: never ask for approval before running tools.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        default=False,
        help="Start in plan mode: read-only tools for exploration and planning.",
    )
    parser.add_argument(
        "--enabled-tools",
        action="append",
        metavar="TOOL",
        help="Enable specific tools. Disables all other tools. Can be specified multiple times.",
    )
    continuation_group = parser.add_mutually_exclusive_group()
    continuation_group.add_argument(
        "-c",
        "--continue",
        action="store_true",
        dest="continue_session",
        help="Continue from the most recent saved session",
    )
    continuation_group.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume a specific session by its ID (supports partial matching)",
    )
    parser.add_argument(
        "initial_prompt",
        nargs="?",
        metavar="PROMPT",
        help="Initial prompt to start the interactive session with.",
    )
    return parser.parse_args(argv)


def _load_config_allow_missing_api(
    agent: str | None, initial_mode_kwargs: dict[str, object]
) -> tuple[VibeConfig | None, bool]:
    try:
        return VibeConfig.load(agent, **initial_mode_kwargs), False
    except MissingAPIKeyError:
        return None, True


def main(argv: list[str] | None = None) -> None:
    args = parse_child_arguments(argv)

    # Maintain parity with the primary CLI parser so shared helpers work.
    args.prompt = None
    args.setup = False

    load_api_keys_from_env()
    unlock_config_paths()
    bootstrap_config_files()

    initial_mode = get_initial_mode(args)
    config, needs_onboarding = _load_config_allow_missing_api(
        args.agent, initial_mode.config_overrides
    )

    # If no config yet due to missing API key, create a default one for the UI.
    # Onboarding will run inside the served UI if needs_onboarding is True.
    if config is None:
        fallback_config = VibeConfig.create_default()
        fallback_config.update(initial_mode.config_overrides)
        config = VibeConfig.model_construct(**fallback_config)

    if args.enabled_tools:
        config.enabled_tools = args.enabled_tools

    loaded_messages = None if needs_onboarding else load_session(args, config)

    try:
        run_textual_ui(
            config,
            initial_mode=initial_mode,
            enable_streaming=True,
            initial_prompt=args.initial_prompt,
            loaded_messages=loaded_messages,
            needs_onboarding=needs_onboarding,
            agent_name=args.agent,
            initial_mode_overrides=initial_mode.config_overrides,
        )
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)


if __name__ == "__main__":
    main()
