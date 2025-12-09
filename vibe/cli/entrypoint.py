from __future__ import annotations

import argparse
import sys

from rich import print as rprint

from vibe.cli.textual_ui.app import run_textual_ui
from vibe.core.config import (
    CONFIG_FILE,
    HISTORY_FILE,
    INSTRUCTIONS_FILE,
    MissingAPIKeyError,
    MissingPromptFileError,
    VibeConfig,
    load_api_keys_from_env,
)
from vibe.core.interaction_logger import InteractionLogger
from vibe.core.programmatic import run_programmatic
from vibe.core.types import OutputFormat, ResumeSessionInfo
from vibe.core.utils import ConversationLimitException
from vibe.setup.onboarding import run_onboarding


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mistral Vibe interactive CLI")
    parser.add_argument(
        "initial_prompt",
        nargs="?",
        metavar="PROMPT",
        help="Initial prompt to start the interactive session with.",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        nargs="?",
        const="",
        metavar="TEXT",
        help="Run in programmatic mode: send prompt, auto-approve all tools, "
        "output response, and exit.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=False,
        help="Automatically approve all tool executions.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        metavar="N",
        help="Maximum number of assistant turns "
        "(only applies in programmatic mode with -p).",
    )
    parser.add_argument(
        "--max-price",
        type=float,
        metavar="DOLLARS",
        help="Maximum cost in dollars (only applies in programmatic mode with -p). "
        "Session will be interrupted if cost exceeds this limit.",
    )
    parser.add_argument(
        "--enabled-tools",
        action="append",
        metavar="TOOL",
        help="Enable specific tools. In programmatic mode (-p), this disables "
        "all other tools. "
        "Can use exact names, glob patterns (e.g., 'bash*'), or "
        "regex with 're:' prefix. Can be specified multiple times.",
    )
    parser.add_argument(
        "--output",
        type=str,
        choices=["text", "json", "streaming"],
        default="text",
        help="Output format for programmatic mode (-p): 'text' "
        "for human-readable (default), 'json' for all messages at end, "
        "'streaming' for newline-delimited JSON per message.",
    )
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default=None,
        help="Load agent configuration from ~/.vibe/agents/NAME.toml",
    )
    parser.add_argument("--setup", action="store_true", help="Setup API key and exit")

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
    return parser.parse_args()


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


def load_config_or_exit(agent: str | None = None) -> VibeConfig:
    try:
        return VibeConfig.load(agent)
    except MissingAPIKeyError:
        run_onboarding()
        return VibeConfig.load(agent)
    except MissingPromptFileError as e:
        rprint(f"[yellow]Invalid system prompt id: {e}[/]")
        sys.exit(1)
    except ValueError as e:
        rprint(f"[yellow]{e}[/]")
        sys.exit(1)


def main() -> None:  # noqa: PLR0912, PLR0915
    load_api_keys_from_env()
    args = parse_arguments()

    if args.setup:
        run_onboarding()
        sys.exit(0)
    try:
        if not CONFIG_FILE.exists():
            try:
                VibeConfig.save_updates(VibeConfig.create_default())
            except Exception as e:
                rprint(f"[yellow]Could not create default config file: {e}[/]")

        if not INSTRUCTIONS_FILE.exists():
            try:
                INSTRUCTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
                INSTRUCTIONS_FILE.touch()
            except Exception as e:
                rprint(f"[yellow]Could not create instructions file: {e}[/]")

        if not HISTORY_FILE.exists():
            try:
                HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
                HISTORY_FILE.write_text("Hello Vibe!\n", "utf-8")
            except Exception as e:
                rprint(f"[yellow]Could not create history file: {e}[/]")

        config = load_config_or_exit(args.agent)

        if args.enabled_tools:
            config.enabled_tools = args.enabled_tools

        loaded_messages = None
        session_info = None

        if args.continue_session or args.resume:
            if not config.session_logging.enabled:
                rprint(
                    "[red]Session logging is disabled. "
                    "Enable it in config to use --continue or --resume[/]"
                )
                sys.exit(1)

            session_to_load = None
            if args.continue_session:
                session_to_load = InteractionLogger.find_latest_session(
                    config.session_logging
                )
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
                loaded_messages, metadata = InteractionLogger.load_session(
                    session_to_load
                )
                session_id = metadata.get("session_id", "unknown")[:8]
                session_time = metadata.get("start_time", "unknown time")

                session_info = ResumeSessionInfo(
                    type="continue" if args.continue_session else "resume",
                    session_id=session_id,
                    session_time=session_time,
                )
            except Exception as e:
                rprint(f"[red]Failed to load session: {e}[/]")
                sys.exit(1)

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
                auto_approve=args.auto_approve,
                enable_streaming=True,
                initial_prompt=args.initial_prompt or stdin_prompt,
                loaded_messages=loaded_messages,
                session_info=session_info,
            )

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]Bye![/]")
        sys.exit(0)


if __name__ == "__main__":
    main()
