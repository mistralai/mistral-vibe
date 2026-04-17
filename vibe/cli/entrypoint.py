from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from rich import print as rprint

from vibe import __version__
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config.harness_files import init_harness_files_manager
from vibe.core.trusted_folders import find_trustable_files, trusted_folders_manager
from vibe.setup.trusted_folders.trust_folder_dialog import (
    TrustDialogQuitException,
    ask_trust_folder,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mistral Vibe interactive CLI")
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
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
        default=BuiltinAgentName.DEFAULT,
        help="Agent to use (builtin: default, plan, accept-edits, auto-approve, "
        "or custom from ~/.vibe/agents/NAME.toml)",
    )
    parser.add_argument("--setup", action="store_true", help="Setup API key and exit")
    parser.add_argument(
        "--workdir",
        type=Path,
        metavar="DIR",
        help="Change to this directory before running",
    )

    # Feature flag for teleport, not exposed to the user yet
    parser.add_argument("--teleport", action="store_true", help=argparse.SUPPRESS)

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
        nargs="?",
        const=True,
        default=None,
        metavar="SESSION_ID",
        help="Resume a session. Without SESSION_ID, shows an interactive picker.",
    )
    return parser.parse_args()


def check_and_resolve_trusted_folder() -> None:
    try:
        cwd = Path.cwd()
    except FileNotFoundError:
        rprint(
            "[red]Error: Current working directory no longer exists.[/]\n"
            "[yellow]The directory you started vibe from has been deleted. "
            "Please change to an existing directory and try again, "
            "or use --workdir to specify a working directory.[/]"
        )
        sys.exit(1)

    if cwd.resolve() == Path.home().resolve():
        return

    detected_files = find_trustable_files(cwd)

    if not detected_files:
        return

    is_folder_trusted = trusted_folders_manager.is_trusted(cwd)

    if is_folder_trusted is not None:
        return

    try:
        is_folder_trusted = ask_trust_folder(cwd, detected_files)
    except (KeyboardInterrupt, EOFError, TrustDialogQuitException):
        sys.exit(0)
    except Exception as e:
        rprint(f"[yellow]Error showing trust dialog: {e}[/]")
        return

    if is_folder_trusted is True:
        trusted_folders_manager.add_trusted(cwd)
    elif is_folder_trusted is False:
        trusted_folders_manager.add_untrusted(cwd)


def run_image_command() -> None:
    """Handle the `vibe image` subcommand."""
    parser = argparse.ArgumentParser(
        prog="vibe image",
        description=(
            "Analyze images using Mistral vision models.\n\n"
            "Examples:\n"
            "  vibe image photo.jpg                    # describe an image\n"
            "  vibe image photo.jpg -p 'add snow'     # vision with prompt\n"
            "  vibe image -p 'a black cat'            # text-to-image (coming soon)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        metavar="PATH",
        help="Path to the input image file.",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        default=None,
        metavar="TEXT",
        help="Prompt to send alongside the image.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Save result to this file.",
    )
    parser.add_argument(
        "--model",
        default="mistral-small-2506",
        metavar="MODEL",
        help="Mistral model to use (default: mistral-small-2506).",
    )
    parser.add_argument(
        "--format",
        choices=["png", "jpg"],
        default="png",
        dest="fmt",
        help="Output image format when generating (default: png).",
    )
    parser.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help="Response language (e.g. french). Defaults to prompt language.",
    )

    args = parser.parse_args(sys.argv[2:])

    from vibe.core.tools.base import BaseToolState, ToolError
    from vibe.core.tools.builtins.image import Image, ImageArgs, ImageToolConfig

    tool = Image(config_getter=lambda: ImageToolConfig(), state=BaseToolState())

    async def run() -> None:
        async for result in tool.run(
            ImageArgs(
                path=args.path,
                prompt=args.prompt,
                output=args.output,
                model=args.model,
                language=args.language,
            )
        ):
            if hasattr(result, "text"):
                print(result.text)
                if result.saved_to:
                    rprint(f"[green]Saved to {result.saved_to}[/]", file=sys.stderr)

    try:
        asyncio.run(run())
    except ToolError as e:
        rprint(f"[yellow]⚠ {e}[/]", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "image":
        run_image_command()
        return

    args = parse_arguments()

    if args.workdir:
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            rprint(
                f"[red]Error: --workdir does not exist or is not a directory: {workdir}[/]"
            )
            sys.exit(1)
        os.chdir(workdir)

    is_interactive = args.prompt is None
    if is_interactive:
        check_and_resolve_trusted_folder()
    init_harness_files_manager("user", "project")

    from vibe.cli.cli import run_cli

    run_cli(args)


if __name__ == "__main__":
    main()
