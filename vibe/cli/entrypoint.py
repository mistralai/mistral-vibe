from __future__ import annotations

# isort: off
# Capture the process-start monotonic timestamp as early as possible (before
# any heavier imports below) so the vibe.startup metric measures from true
# process start. Re-exported for any consumer that needs the same anchor.
from vibe.cli.process_start import PROCESS_START_MONOTONIC as PROCESS_START_MONOTONIC
# isort: on

import argparse
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from vibe import __version__
from vibe._experimental_harness import add_experimental_harness_argument

# Anything heavier than argparse is imported inside the functions below, after
# argument parsing, so that --help/--version don't pay for the config stack
# (pydantic, textual, rich) at import time.

if TYPE_CHECKING:
    from vibe.core.git.worktree import PreparedWorktree, WorktreeCleanupState


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Mistral Vibe interactive CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  mcp            Manage MCP server configuration (vibe mcp --help).\n\n"
            "Environment variables:\n"
            "  VIBE_HOME       Override the Vibe home directory (default: ~/.vibe)\n"
            "  LOG_LEVEL       Logging level: DEBUG, INFO, WARNING (default), ERROR, CRITICAL.\n"
            "                  Also set via log_level in config.toml or /log-level at runtime.\n"
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
        help="Agent to use (builtin: ask, plan, accept-edits, auto-approve, "
        "or custom from ~/.vibe/agents/NAME.toml). Defaults to the "
        "'default_agent' config setting in both interactive and programmatic "
        "(-p/--prompt) mode.",
    )
    add_experimental_harness_argument(parser)
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
        nargs="?",
        const=True,
        default=None,
        metavar="NAME",
        help="Run inside a git worktree under $VIBE_HOME/worktrees. With NAME, "
        "create (or reuse) a worktree and branch named NAME. Without NAME, "
        "create a new one named after the prompt (or a random slug) on a "
        "vibe/<name> branch. Implicitly trusted for the session. Ignored with "
        "--setup and --check-upgrade.",
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


def _enter_worktree(args: argparse.Namespace) -> PreparedWorktree:
    """Prepare the requested worktree, hold it, and chdir into it."""
    from rich import print as rprint

    from vibe.core.git.errors import GitError
    from vibe.core.git.worktree import ManagedWorktree, WorktreeRepository

    requested = "" if args.worktree is True else f" {args.worktree!r}"
    rprint(f"[dim]Preparing worktree{requested}...[/]", file=sys.stderr)
    try:
        with WorktreeRepository.open(Path.cwd()) as repository:
            if args.worktree is True:
                prompt = args.prompt or args.initial_prompt
                session = repository.prepare_auto(
                    prompt=prompt, suggested_name=_suggest_worktree_name(prompt)
                )
            else:
                session = repository.prepare(args.worktree)
    except GitError as e:
        rprint(f"[red]Error: {e}[/]")
        sys.exit(1)
    rprint(f"[dim]Using worktree: {session.path}[/]", file=sys.stderr)
    if managed := ManagedWorktree.at(session.root):
        managed.hold(_cli_worktree_holder())
    os.chdir(session.path)
    return session


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


# One holder per CLI process. An app-server sweeping the same repo reads these
# markers to tell a live worktree from an abandoned one, so a CLI session that
# never registered could have its worktree removed underneath it.
def _cli_worktree_holder() -> str:
    return f"cli-{os.getpid()}"


def _cleanup_worktree_on_exit(worktree: PreparedWorktree) -> None:
    from rich import print as rprint

    from vibe.core.git.errors import GitError
    from vibe.core.git.worktree import ManagedWorktree

    # Our marker stays up for all of this, prompts included: an app-server
    # sweeping the same repo treats an unheld worktree as fair game, and the
    # user can sit at a prompt indefinitely. It is dropped by the caller, which
    # acquired it, on every path rather than only this one.
    holder = _cli_worktree_holder()
    try:
        cleanup_state = worktree.inspect_for_cleanup()
    except GitError as e:
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

    # Re-read after the prompts: an app-server session can join this
    # worktree through the composer while the user sits at them, and
    # removing it then would drop a live session into a deleted directory.
    # Our own marker is still up, so discount it.
    managed = ManagedWorktree.at(worktree.root)
    held_by = frozenset() if managed is None else managed.holders()
    if remaining := held_by - {holder}:
        rprint(
            f"[dim]Keeping worktree {worktree.root}: in use by "
            f"{len(remaining)} other session(s)[/]",
            file=sys.stderr,
        )
        return

    try:
        rprint(f"[dim]Removing worktree: {worktree.root}[/]", file=sys.stderr)
        worktree.leave_if_current_directory()
        worktree.remove(delete_branch=delete_branch)
    except GitError as e:
        rprint(f"[yellow]Could not remove worktree: {e}[/]", file=sys.stderr)
        return

    if managed is not None:
        managed.forget()
    rprint(f"[dim]Removed worktree: {worktree.root}[/]", file=sys.stderr)
    if not delete_branch:
        rprint(f"[dim]Kept branch: {worktree.branch}[/]", file=sys.stderr)


def _suggest_worktree_name(prompt: str | None) -> str | None:
    # Bare `vibe --worktree` has nothing to name from, so skip the dotenv read
    # and the event loop rather than spinning both up to be told None.
    if not prompt:
        return None

    import asyncio

    from vibe.core.config.harness_files import init_harness_files_manager
    from vibe.core.config.vibe_schema import load_dotenv_values
    from vibe.core.git.worktree.naming_model import suggest_worktree_name

    # Worktrees are prepared before run_cli, so neither of the things the
    # suggestion needs has happened yet. ~/.vibe/.env is not in os.environ, so a
    # key that lives only there would read as absent; and loading config
    # resolves prompts through the global harness manager, which is not
    # initialised until later in main(). Both calls are idempotent -- the second
    # init with these same sources returns without replacing the singleton.
    load_dotenv_values()
    init_harness_files_manager("user", "project")
    return asyncio.run(suggest_worktree_name(prompt, cwd=Path.cwd()))


def _set_process_title() -> None:
    # Cosmetic: renames the process from "python3" to "Vibe CLI" in ps/top and
    # Activity Monitor on Linux/macOS so it can be spotted and killed; concurrent
    # instances are told apart by the process manager's PID column. No-op for
    # Windows Task Manager. Must never block startup, hence the broad guard.
    try:
        import setproctitle

        from vibe.cli._process_title import process_name

        setproctitle.setproctitle(process_name())
    except Exception:
        pass


def main() -> None:
    _set_process_title()

    from vibe.core.utils.windows_asyncio import (
        silence_proactor_transport_teardown_warnings,
    )

    silence_proactor_transport_teardown_warnings()

    if sys.argv[1:2] == ["mcp"]:
        from vibe.cli.mcp_command import run_mcp_cli

        run_mcp_cli(sys.argv[2:])
        return

    args = parse_arguments()
    worktree_session: PreparedWorktree | None = None

    from rich import print as rprint

    from vibe.core.config.harness_files import init_harness_files_manager
    from vibe.core.paths import LOG_FILE
    from vibe.observability.logging import init_file_logging

    init_file_logging(LOG_FILE.path)

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
        worktree_session = _enter_worktree(args)

    try:
        Path.cwd()
    except FileNotFoundError:
        rprint(
            "[red]Error: Current working directory no longer exists.[/]\n"
            "[yellow]The directory you started vibe from has been deleted. "
            "Please change to an existing directory and try again, "
            "or use --workdir to specify a working directory.[/]"
        )
        sys.exit(1)

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

    args.add_dir = [str(path) for path in additional_dirs]
    init_harness_files_manager("user", "project")

    _run_cli_with_worktree_cleanup(args, worktree_session)


def _run_cli_with_worktree_cleanup(
    args: argparse.Namespace, worktree_session: PreparedWorktree | None
) -> None:
    from vibe.cli.cli import run_cli

    session_started = False
    try:
        run_cli(args)
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
        # Released for every worktree this run held, not only the ones eligible
        # for cleanup above. A marker left behind reads as a live session
        # forever: releases report the worktree in use, and the sweep only
        # reclaims reservations that never became one, so nothing would remove
        # that checkout again.
        if worktree_session is not None:
            from vibe.core.git.worktree import ManagedWorktree

            if managed := ManagedWorktree.at(worktree_session.root):
                managed.release_holder(_cli_worktree_holder())


if __name__ == "__main__":
    main()
