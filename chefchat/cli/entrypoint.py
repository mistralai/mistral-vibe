"""ChefChat CLI Entrypoint.

Main entry point for the ChefChat application.
TUI is the default mode; use --repl flag for the classic REPL interface.
"""

from __future__ import annotations

import argparse
import sys

from rich import print as rprint

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
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="ChefChat - The Michelin Star AI-Engineer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  vibe                          # Start TUI (default)
  vibe --repl                   # Start classic REPL
  vibe --setup                  # Run setup wizard
  vibe "Create a REST API"      # Programmatic mode with prompt
        """,
    )
    parser.add_argument(
        "prompt", nargs="*", default=None, help="The prompt to send (programmatic mode)"
    )
    # TUI is now default, use --repl for classic REPL
    parser.add_argument(
        "--repl", action="store_true", help="Launch classic REPL mode instead of TUI"
    )
    parser.add_argument("--tui", action="store_true", help="Launch TUI mode (default)")
    parser.add_argument("--setup", action="store_true", help="Launch the setup wizard")
    parser.add_argument("--agent", default=None, help="The name of the agent to use")
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continue from the last session",
    )
    parser.add_argument("--resume", dest="resume", help="Resume a specific session ID")
    parser.add_argument(
        "-y", "--auto-approve", action="store_true", help="Auto-approve all tool calls"
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Maximum turns for programmatic mode",
    )
    parser.add_argument(
        "--max-price",
        type=float,
        default=None,
        help="Maximum price for programmatic mode",
    )
    parser.add_argument(
        "--format",
        default="text",
        choices=["text", "json", "markdown"],
        help="Output format",
    )
    parser.add_argument(
        "--tools", dest="enabled_tools", nargs="+", help="List of tools to enable"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose debug output"
    )
    return parser.parse_args()


def get_prompt_from_stdin() -> str | None:
    """Get prompt from stdin if available.

    Returns:
        Prompt string or None if stdin is a tty
    """
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return None


def load_config_or_exit(agent: str | None = None) -> VibeConfig:
    """Load configuration or run onboarding if needed.

    Args:
        agent: Optional agent name to load

    Returns:
        Loaded configuration
    """
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


def _ensure_config_files() -> None:
    """Ensure config and history files exist."""
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


def _handle_session_resume(
    args: argparse.Namespace, config: VibeConfig
) -> tuple[list | None, ResumeSessionInfo | None]:
    """Handle session resume/continue logic.

    Args:
        args: Parsed arguments
        config: Configuration

    Returns:
        Tuple of (loaded_messages, session_info)
    """
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
            loaded_messages, metadata = InteractionLogger.load_session(session_to_load)
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

    return loaded_messages, session_info


def main() -> None:
    """Main entry point for ChefChat.

    TUI is the default mode. Use --repl for classic REPL.
    """
    # Force UTF-8 encoding for stdout on Windows to support emojis
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_api_keys_from_env()
    args = parse_arguments()

    explicit_tui = bool(getattr(args, "tui", False))

    # Handle setup wizard
    if args.setup:
        from chefchat.setup.onboarding import run_onboarding

        run_onboarding()
        sys.exit(0)

    # Ensure config and history files exist
    _ensure_config_files()

    try:
        config = load_config_or_exit(agent=args.agent)

        if args.enabled_tools:
            config.enabled_tools = args.enabled_tools

        # Handle session resume
        loaded_messages, _ = _handle_session_resume(args, config)

        # Check for programmatic mode (prompt provided)
        stdin_prompt = get_prompt_from_stdin()
        if args.prompt:
            programmatic_prompt = " ".join(args.prompt) if args.prompt else stdin_prompt
            if not programmatic_prompt:
                print(
                    "Error: No prompt provided for programmatic mode", file=sys.stderr
                )
                sys.exit(1)

            output_format = OutputFormat(
                args.format if hasattr(args, "format") else "text"
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

        # Interactive mode
        if args.repl:
            rprint("[bold blue]🔪 Starting REPL...[/]")
            # Classic REPL mode (legacy)
            initial_mode = mode_from_auto_approve(args.auto_approve)
            run_repl(config, initial_mode=initial_mode)
        else:
            rprint("[bold blue]👨‍🍳 Starting TUI...[/]")
            # TUI is the default (new standard)
            try:
                # Set environment to force Textual to work
                import os
                os.environ.setdefault("FORCE_COLOR", "1")
                os.environ.setdefault("TERM", "xterm-256color")
                
                from chefchat.interface.tui import run as run_tui

                run_tui(verbose=bool(getattr(args, "verbose", False)))
            except ImportError as e:
                rprint(f"[red]❌ Failed to import TUI: {e}[/]")
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                if explicit_tui:
                    raise
                rprint("\n[yellow]⚠️  Falling back to REPL mode...[/]")
                initial_mode = mode_from_auto_approve(args.auto_approve)
                run_repl(config, initial_mode=initial_mode)
            except Exception as e:
                rprint(f"[red]❌ Error launching TUI: {e}[/]")
                
                # Show debug info in verbose mode
                if args.verbose:
                    import traceback
                    traceback.print_exc()

                if explicit_tui:
                    raise

                rprint("\n[yellow]⚠️  Falling back to REPL mode...[/]")
                # Fallback to REPL
                initial_mode = mode_from_auto_approve(args.auto_approve)
                run_repl(config, initial_mode=initial_mode)

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]👋 Bye![/]")
        sys.exit(0)
    except Exception as e:
        rprint(f"[red]Fatal error: {e}[/]")
        if args.verbose if hasattr(args, "verbose") else False:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()