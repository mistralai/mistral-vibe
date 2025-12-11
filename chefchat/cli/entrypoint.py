from __future__ import annotations

import argparse
import sys

from rich import print as rprint

# NOTE: Legacy textual_ui removed — REPL is now the only interactive mode
from chefchat.cli.mode_manager import mode_from_auto_approve
from chefchat.cli.repl import run_repl
from chefchat.core.config import (
    CONFIG_FILE,
    HISTORY_FILE,
    INSTRUCTIONS_FILE,
    MissingAPIKeyError,
    MissingPromptFileError,
    VibeConfig,
    load_api_keys_from_env,
)
from chefchat.core.interaction_logger import InteractionLogger
from chefchat.core.programmatic import run_programmatic
from chefchat.core.types import OutputFormat, ResumeSessionInfo
from chefchat.core.utils import ConversationLimitException


def parse_arguments() -> argparse.Namespace:
    # ... (rest of parse_arguments) ...
    pass

def get_prompt_from_stdin() -> str | None:
    # ... (rest of get_prompt_from_stdin) ...
    pass

def load_config_or_exit(agent: str | None = None) -> VibeConfig:
    try:
        return VibeConfig.load(agent)
    except MissingAPIKeyError:
        # Lazy import run_onboarding here to break circular dependency
        from chefchat.setup.onboarding import run_onboarding
        run_onboarding()
        return VibeConfig.load(agent)
    except MissingPromptFileError as e:
        rprint(f"[yellow]Invalid system prompt id: {e}[/]")
        sys.exit(1)
    except ValueError as e:
        rprint(f"[yellow]{e}[/]")
        sys.exit(1)

def main() -> None:  # noqa: PLR0912, PLR0915
    # Force UTF-8 encoding for stdout on Windows to support emojis
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_api_keys_from_env()
    args = parse_arguments()

    if args.setup:
        # Lazy import run_onboarding here to break circular dependency
        from chefchat.setup.onboarding import run_onboarding
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
            # REPL is now the only interactive mode (textual_ui removed)
            initial_mode = mode_from_auto_approve(args.auto_approve)
            run_repl(config, initial_mode=initial_mode)

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]Bye![/]")
        sys.exit(0)


if __name__ == "__main__":
    main()
