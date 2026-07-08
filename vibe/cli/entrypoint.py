from __future__ import annotations

import argparse
from collections.abc import Callable
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from vibe import __version__

# Anything heavier than argparse is imported inside the functions below, after
# argument parsing, so that --help/--version don't pay for the config stack
# (pydantic, textual, rich) at import time.

if TYPE_CHECKING:
    from vibe.core.worktree import PreparedWorktree, WorktreeCleanupState


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Mistral Vibe interactive CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  VIBE_HOME       Override the Vibe home directory (default: ~/.vibe)\n"
            "  LOG_LEVEL       Logging level: DEBUG, INFO, WARNING (default), ERROR, CRITICAL.\n"
            "                  Logs are written to $VIBE_HOME/logs/vibe.log.\n"
            "  LOG_MAX_BYTES   Max size of vibe.log before rotation (default: 10485760).\n"
            "  VIBE_*          Override any config field (e.g. VIBE_ACTIVE_MODEL=local)."
        ),
    )
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
        help="Run in programmatic mode: send prompt, output response, and exit. "
        "Tool approval follows the selected --agent (or 'default_agent' config); "
        "pass --auto-approve or --yolo to allow all tool calls.",
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
        "--max-tokens",
        type=int,
        metavar="N",
        help="Maximum total prompt + completion tokens across the session "
        "(only applies in programmatic mode with -p). "
        "Session will be interrupted if usage exceeds this limit.",
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
        "--disabled-tools",
        action="append",
        metavar="TOOL",
        help="Disable specific tools after --enabled-tools filtering. "
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
        help="Agent to use (builtin: default, plan, accept-edits, auto-approve, "
        "or custom from ~/.vibe/agents/NAME.toml). Defaults to the "
        "'default_agent' config setting in both interactive and programmatic "
        "(-p/--prompt) mode.",
    )
    parser.add_argument(
        "--auto-approve",
        "--yolo",
        action="store_true",
        help="Approves all tool calls without prompting for the selected agent.",
    )
    parser.add_argument("--setup", action="store_true", help="Setup API key and exit")
    parser.add_argument(
        "--check-upgrade",
        action="store_true",
        help="Check for a Vibe update now, prompt to install it, and exit",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        metavar="DIR",
        help="Change to this directory before running",
    )
    parser.add_argument(
        "--worktree",
        metavar="NAME",
        help="Create (or reuse) a git worktree under $VIBE_HOME/worktrees on "
        "a branch named NAME and run inside it. Implicitly trusted for the "
        "session. Ignored with --setup and --check-upgrade.",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        metavar="DIR",
        default=[],
        help="Additional working directory for file access and context. "
        "Implicitly trusted for the session (same semantics as --trust). "
        "Can be specified multiple times.",
    )
    parser.add_argument(
        "--trust",
        action="store_true",
        help="Trust the working directory for this invocation only (not "
        "persisted to trusted_folders.toml). Skips the trust prompt. "
        "Use this for non-interactive automation.",
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


def check_and_resolve_trusted_folder(cwd: Path) -> None:
    from rich import print as rprint

    from vibe.core.trusted_folders import (
        apply_workspace_trust_decision,
        maybe_build_workspace_trust_prompt,
    )
    from vibe.setup.trusted_folders.trust_folder_dialog import (
        TrustDialogQuitException,
        ask_trust_folder,
    )

    prompt = maybe_build_workspace_trust_prompt(cwd)
    if prompt is None:
        return

    try:
        decision = ask_trust_folder(
            prompt.cwd,
            prompt.repo_root,
            prompt.detected_files,
            repo_detected_files=prompt.repo_detected_files,
            offer_repo_trust=prompt.offer_repo_trust,
            repo_explicitly_untrusted=prompt.repo_explicitly_untrusted,
        )
    except (KeyboardInterrupt, EOFError, TrustDialogQuitException):
        sys.exit(0)
    except Exception as e:
        rprint(f"[yellow]Error showing trust dialog: {e}[/]")
        return

    if decision is not None:
        apply_workspace_trust_decision(prompt, decision)


def _prompt_remove_worktree(
    worktree: PreparedWorktree, cleanup_state: WorktreeCleanupState
) -> bool:
    from rich import print as rprint

    reasons = ", ".join(cleanup_state.reasons)
    rprint(f"[yellow]Worktree {worktree.name!r} has {reasons}.[/]", file=sys.stderr)
    rprint(
        "[yellow]Remove it and delete its branch? This discards worktree changes, "
        "untracked files, and commits.[/]",
        file=sys.stderr,
    )
    sys.stderr.write("Remove worktree? [y/N] ")
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n")
        return False
    return answer in {"y", "yes", "remove"}


def _prompt_delete_attached_branch(worktree: PreparedWorktree) -> bool:
    from rich import print as rprint

    rprint(
        f"[yellow]Branch {worktree.branch!r} existed before this session "
        f"and was attached, not created by Vibe.[/]",
        file=sys.stderr,
    )
    sys.stderr.write(f"Also delete branch {worktree.branch!r}? [y/N] ")
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n")
        return False
    return answer in {"y", "yes", "delete"}


def _cleanup_worktree_on_exit(worktree: PreparedWorktree) -> None:
    from rich import print as rprint

    from vibe.core.worktree import (
        WorktreeError,
        inspect_worktree_for_cleanup,
        remove_worktree,
    )

    try:
        cleanup_state = inspect_worktree_for_cleanup(worktree)
    except WorktreeError as e:
        rprint(
            f"[yellow]Could not inspect worktree for cleanup: {e}[/]", file=sys.stderr
        )
        return

    if not cleanup_state.is_clean and not _prompt_remove_worktree(
        worktree, cleanup_state
    ):
        rprint(f"[dim]Keeping worktree: {worktree.root}[/]", file=sys.stderr)
        return

    delete_branch = worktree.branch_created or _prompt_delete_attached_branch(worktree)

    try:
        rprint(f"[dim]Removing worktree: {worktree.root}[/]", file=sys.stderr)
        remove_worktree(worktree, delete_branch=delete_branch)
    except WorktreeError as e:
        rprint(f"[yellow]Could not remove worktree: {e}[/]", file=sys.stderr)
        return

    rprint(f"[dim]Removed worktree: {worktree.root}[/]", file=sys.stderr)
    if not delete_branch:
        rprint(f"[dim]Kept branch: {worktree.branch}[/]", file=sys.stderr)


def main() -> None:
    args = parse_arguments()
    worktree_session: PreparedWorktree | None = None

    from rich import print as rprint

    from vibe.core.config.harness_files import init_harness_files_manager
    from vibe.core.trusted_folders import trusted_folders_manager

    if args.workdir:
        workdir = args.workdir.expanduser().resolve()
        if not workdir.is_dir():
            rprint(
                f"[red]Error: --workdir does not exist or is not a directory: {workdir}[/]"
            )
            sys.exit(1)
        os.chdir(workdir)

    # Must run before `cwd` is read and before run_cli so that session lookups
    # (-c / --resume picker) scope to the worktree directory.
    if args.worktree and not (args.setup or args.check_upgrade):
        from vibe.core.worktree import WorktreeError, prepare_worktree_session

        rprint(f"[dim]Preparing worktree {args.worktree!r}...[/]", file=sys.stderr)
        try:
            worktree_session = prepare_worktree_session(args.worktree, Path.cwd())
        except WorktreeError as e:
            rprint(f"[red]Error: {e}[/]")
            sys.exit(1)
        target = worktree_session.path
        rprint(f"[dim]Using worktree: {target}[/]", file=sys.stderr)
        os.chdir(target)

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

    if args.trust or args.worktree:
        trusted_folders_manager.trust_for_session(cwd)

    additional_dirs: list[Path] = []
    for d in args.add_dir:
        resolved = Path(d).expanduser().resolve()
        if not resolved.is_dir():
            rprint(
                f"[red]Error: --add-dir path does not exist "
                f"or is not a directory: {d}[/]"
            )
            sys.exit(1)
        additional_dirs.append(resolved)
        trusted_folders_manager.trust_for_session(resolved)

    init_harness_files_manager("user", "project", additional_dirs=additional_dirs)

    resolve_trusted_folder: Callable[[], None] | None = None
    if args.prompt is None and not args.check_upgrade:

        def _resolve_trusted_folder() -> None:
            check_and_resolve_trusted_folder(cwd)

        resolve_trusted_folder = _resolve_trusted_folder

    _run_cli_with_worktree_cleanup(args, worktree_session, resolve_trusted_folder)


def _run_cli_with_worktree_cleanup(
    args: argparse.Namespace,
    worktree_session: PreparedWorktree | None,
    resolve_trusted_folder: Callable[[], None] | None,
) -> None:
    from vibe.cli.cli import run_cli

    session_started = False
    try:
        run_cli(args, resolve_trusted_folder=resolve_trusted_folder)
        session_started = True
    except SystemExit as e:
        session_started = e.code in {0, None}
        raise
    finally:
        # Only auto-clean worktrees Vibe created this run, and only once a
        # session actually ran — a startup failure (bad config, --continue with
        # no sessions) must not delete a reused worktree or its branch.
        if (
            worktree_session is not None
            and worktree_session.created
            and args.prompt is None
            and session_started
        ):
            _cleanup_worktree_on_exit(worktree_session)


if __name__ == "__main__":
    main()
