from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from rich import print as rprint

from vibe import __version__
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.paths.config_paths import unlock_config_paths
from vibe.core.trusted_folders import has_trustable_content, trusted_folders_manager
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
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Create a new git worktree for this session, allowing multiple "
        "concurrent sessions in the same repository. Each session gets its own "
        "working directory and branch.",
    )
    parser.add_argument(
        "--worktree-branch",
        metavar="BRANCH",
        help="Branch name for the worktree (only with --worktree). "
        "If not specified, a new branch is created.",
    )
    parser.add_argument(
        "--worktree-name",
        metavar="NAME",
        help="Custom name for the worktree (only with --worktree). "
        "If not specified, a unique name is generated.",
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
        metavar="SESSION_ID",
        help="Resume a specific session by its ID (supports partial matching)",
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

    if not has_trustable_content(cwd) or cwd.resolve() == Path.home().resolve():
        return

    is_folder_trusted = trusted_folders_manager.is_trusted(cwd)

    if is_folder_trusted is not None:
        return

    try:
        is_folder_trusted = ask_trust_folder(cwd)
    except (KeyboardInterrupt, EOFError, TrustDialogQuitException):
        sys.exit(0)
    except Exception as e:
        rprint(f"[yellow]Error showing trust dialog: {e}[/]")
        return

    if is_folder_trusted is True:
        trusted_folders_manager.add_trusted(cwd)
    elif is_folder_trusted is False:
        trusted_folders_manager.add_untrusted(cwd)


def main() -> None:
    args = parse_arguments()

    if args.workdir:
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            rprint(
                f"[red]Error: --workdir does not exist or is not a directory: {workdir}[/]"
            )
            sys.exit(1)
        os.chdir(workdir)

    # Handle worktree mode
    worktree_path = None
    if args.worktree:
        from vibe.core.git_worktree import GitWorktreeError, GitWorktreeManager

        try:
            wt_manager = GitWorktreeManager()
            if not wt_manager.is_git_repository():
                rprint(
                    "[red]Error: --worktree requires a git repository.[/]\n"
                    "[yellow]Please run vibe --worktree from within a git repository.[/]"
                )
                sys.exit(1)

            worktree_info = wt_manager.create_worktree(
                branch=args.worktree_branch,
                name=args.worktree_name,
            )
            worktree_path = worktree_info.path
            os.chdir(worktree_path)
            rprint(f"[dim]Created worktree at: {worktree_path}[/]")
            rprint(f"[dim]Branch: {worktree_info.branch}[/]")
        except GitWorktreeError as e:
            rprint(f"[red]Error creating worktree: {e}[/]")
            sys.exit(1)

    is_interactive = args.prompt is None
    if is_interactive:
        check_and_resolve_trusted_folder()
    unlock_config_paths()

    from vibe.cli.cli import run_cli

    try:
        run_cli(args, worktree_path=worktree_path)
    finally:
        if worktree_path and worktree_path.exists() and args.prompt is not None:
            _cleanup_worktree_prompt(worktree_path)


def _cleanup_worktree_prompt(worktree_path: Path) -> None:
    from vibe.core.git_worktree import GitWorktreeManager

    try:
        if sys.stdin.isatty():
            rprint(f"\n[bold]Worktree at: {worktree_path}[/]")
            answer = input("Delete this worktree? [Y/n] ").strip().lower()
            should_delete = answer in {"", "y", "yes"}
        else:
            should_delete = True

        if should_delete:
            wt_manager = GitWorktreeManager(worktree_path.parent)
            wt_manager.remove_worktree(worktree_path, force=True)
            rprint(f"[dim]Removed worktree: {worktree_path}[/]")
        else:
            rprint(f"[dim]Worktree kept at: {worktree_path}[/]")
    except Exception as e:
        rprint(f"[yellow]Failed to remove worktree: {e}[/]")


if __name__ == "__main__":
    main()
