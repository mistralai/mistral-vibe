from __future__ import annotations

import argparse
import sys

from rich import print as rprint

from vibe.cli.textual_ui.app import run_textual_ui
from vibe.core.config import (
    MissingAPIKeyError,
    MissingPromptFileError,
    VibeConfig,
    load_api_keys_from_env,
)
from vibe.core.interaction_logger import InteractionLogger
from vibe.core.modes import AgentMode
from vibe.core.paths.config_paths import CONFIG_FILE, HISTORY_FILE, INSTRUCTIONS_FILE
from vibe.core.programmatic import run_programmatic
from vibe.core.types import LLMMessage, OutputFormat
from vibe.core.utils import ConversationLimitException
from vibe.setup.onboarding import run_onboarding


def get_initial_mode(args: argparse.Namespace) -> AgentMode:
    if args.plan:
        return AgentMode.PLAN
    if args.auto_approve:
        return AgentMode.AUTO_APPROVE
    return AgentMode.DEFAULT


def get_prompt_from_stdin() -> str | None:
    if sys.stdin.isatty():
        return None
    try:
        if content := sys.stdin.read().strip():
            sys.stdin = sys.__stdin__ = open("/dev/tty")
            return content
    except KeyboardInterrupt:
        pass
    except OSError:
        return None

    return None


def load_config_or_exit(
    agent: str | None = None, mode: AgentMode = AgentMode.DEFAULT
) -> VibeConfig:
    try:
        return VibeConfig.load(agent, **mode.config_overrides)
    except MissingAPIKeyError:
        run_onboarding()
        return VibeConfig.load(agent, **mode.config_overrides)
    except MissingPromptFileError as e:
        rprint(f"[yellow]Invalid system prompt id: {e}[/]")
        sys.exit(1)
    except ValueError as e:
        rprint(f"[yellow]{e}[/]")
        sys.exit(1)


def bootstrap_config_files() -> None:
    if not CONFIG_FILE.path.exists():
        try:
            VibeConfig.save_updates(VibeConfig.create_default())
        except Exception as e:
            rprint(f"[yellow]Could not create default config file: {e}[/]")

    if not INSTRUCTIONS_FILE.path.exists():
        try:
            INSTRUCTIONS_FILE.path.parent.mkdir(parents=True, exist_ok=True)
            INSTRUCTIONS_FILE.path.touch()
        except Exception as e:
            rprint(f"[yellow]Could not create instructions file: {e}[/]")

    if not HISTORY_FILE.path.exists():
        try:
            HISTORY_FILE.path.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.path.write_text("Hello Vibe!\n", "utf-8")
        except Exception as e:
            rprint(f"[yellow]Could not create history file: {e}[/]")


def load_session(
    args: argparse.Namespace, config: VibeConfig
) -> list[LLMMessage] | None:
    if not args.continue_session and not args.resume:
        return None

    if not config.session_logging.enabled:
        rprint(
            "[red]Session logging is disabled. "
            "Enable it in config to use --continue or --resume[/]"
        )
        sys.exit(1)

    session_to_load = None
    if args.continue_session:
        session_to_load = InteractionLogger.find_latest_session(config.session_logging)
        if not session_to_load:
            rprint(
                f"[red]No previous sessions found in "
                f"{config.session_logging.save_dir}[/]"
            )
            sys.exit(1)
    else:
        session_to_load = InteractionLogger.find_session_by_id(
            args.resume, config.session_logging
        )
        if not session_to_load:
            rprint(
                f"[red]Session '{args.resume}' not found in "
                f"{config.session_logging.save_dir}[/]"
            )
            sys.exit(1)

    try:
        loaded_messages, _ = InteractionLogger.load_session(session_to_load)
        return loaded_messages
    except Exception as e:
        rprint(f"[red]Failed to load session: {e}[/]")
        sys.exit(1)


def run_cli(args: argparse.Namespace) -> None:
    load_api_keys_from_env()

    if args.setup:
        run_onboarding()
        sys.exit(0)

    try:
        bootstrap_config_files()

        initial_mode = get_initial_mode(args)
        config = load_config_or_exit(args.agent, initial_mode)

        if args.enabled_tools:
            config.enabled_tools = args.enabled_tools

        loaded_messages = load_session(args, config)

        stdin_prompt = get_prompt_from_stdin()
        if args.prompt is not None:
            programmatic_prompt = args.prompt or stdin_prompt
            if not programmatic_prompt:
                print(
                    "Error: No prompt provided for programmatic mode", file=sys.stderr
                )
                sys.exit(1)
            output_format = OutputFormat(
                args.output if hasattr(args, "output") else "text"
            )

            try:
                final_response = run_programmatic(
                    config=config,
                    prompt=programmatic_prompt,
                    max_turns=args.max_turns,
                    max_price=args.max_price,
                    output_format=output_format,
                    previous_messages=loaded_messages,
                    mode=initial_mode,
                )
                if final_response:
                    print(final_response)
                sys.exit(0)
            except ConversationLimitException as e:
                print(e, file=sys.stderr)
                sys.exit(1)
            except RuntimeError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            run_textual_ui(
                config,
                initial_mode=initial_mode,
                enable_streaming=True,
                initial_prompt=args.initial_prompt or stdin_prompt,
                loaded_messages=loaded_messages,
            )

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]Bye![/]")
        sys.exit(0)
