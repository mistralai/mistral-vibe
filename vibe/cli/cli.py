from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import TYPE_CHECKING, NoReturn

from pydantic import ValidationError
from rich import print as rprint
from rich.markup import escape

from vibe import __version__
from vibe._experimental_harness import ExperimentalHarnessUnavailableError
from vibe.app_server.run_export import HeadlessUsageError, RunOutcome, RunResult
from vibe.cli.headless_run import HeadlessRun, RunReport, RunTerminated
from vibe.cli.session_exit import print_session_resume_message
from vibe.cli.terminal_detect import detect_terminal
from vibe.cli.update_notifier import (
    FileSystemUpdateCacheRepository,
    UpdateCacheRepository,
    UpdateError,
    UpdateGateway,
    create_update_gateway,
    get_pending_update_from_cache,
    get_update_if_available,
    mark_update_as_dismissed,
)
from vibe.cli.update_notifier.update import (
    FORCE_REINSTALL_COMMAND,
    force_reinstall_latest,
    is_uv_tool_install,
)
from vibe.core.config import MissingAPIKeyError, VibeConfigSchema, load_dotenv_values
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layer import ConfigStorageError, LayerImplementationError
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import HISTORY_FILE, bootstrap_vibe_home
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.telemetry.types import LaunchContext
from vibe.observability.logging import logger
from vibe.observability.sentry import init_sentry

# The TUI app, onboarding, update prompt, and programmatic runner are each
# imported at their call site: every launch needs at most one of them, and
# they are too heavy to load speculatively at startup.

if TYPE_CHECKING:
    from vibe.app_server.local import LocalSessionIntent
    from vibe.cli.agent_socket import AgentSocket
    from vibe.setup.update_prompt import UpdatePromptMode


def _build_cli_launch_context() -> LaunchContext:
    return build_launch_context(
        agent_entrypoint="cli",
        agent_version=__version__,
        client_name="vibe_cli",
        client_version=__version__,
        terminal_emulator=detect_terminal(),
    )


def get_prompt_from_stdin() -> str | None:
    if sys.stdin.isatty():
        return None
    try:
        content = sys.stdin.read().strip()
    except KeyboardInterrupt:
        return None
    if content:
        try:
            sys.stdin = sys.__stdin__ = open("/dev/tty")
        except OSError:
            pass
        return content
    return None


def _format_config_validation_error(exc: ValidationError) -> str:
    lines = [f"Invalid configuration ({exc.error_count()} error(s)):"]
    for err in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


class ConfigLoadError(Exception):
    """The config could not be loaded; ``message`` says why, for a person."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def load_config_orchestrator() -> ConfigOrchestrator[VibeConfigSchema]:
    try:
        return asyncio.run(build_default_orchestrator())
    except ValidationError as e:
        raise ConfigLoadError(_format_config_validation_error(e)) from e
    except ConfigStorageError as e:
        raise ConfigLoadError(
            f"Cannot {e.operation} the Vibe config file at {e.path}: "
            f"{e.__cause__}.\nVibe needs read/write access to it. If it is managed "
            "read-only (e.g. symlinked from the Nix store), make it writable or set "
            "VIBE_HOME to a writable directory."
        ) from e
    except ValueError as e:
        raise ConfigLoadError(str(e)) from e


def load_config_orchestrator_or_exit() -> ConfigOrchestrator[VibeConfigSchema]:
    try:
        return load_config_orchestrator()
    except ConfigLoadError as e:
        rprint(f"[yellow]{escape(e.message)}[/]")
        sys.exit(1)


def _missing_api_key_message(error: MissingAPIKeyError) -> str:
    return (
        f"{error}. Set the environment variable (e.g. in ~/.vibe/.env "
        "or your shell), or run `vibe --setup` once interactively."
    )


def require_api_key_or_onboard(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> ConfigOrchestrator[VibeConfigSchema]:
    try:
        orchestrator.config.require_active_provider_api_key()
        return orchestrator
    except MissingAPIKeyError:
        from vibe.setup.onboarding import run_onboarding

        return run_onboarding(
            launch_context=_build_cli_launch_context(), orchestrator=orchestrator
        )


def _agent_selection(args: argparse.Namespace) -> tuple[str | None, bool]:
    from vibe.core.agents.models import BuiltinAgentName

    if args.auto_approve and not args.agent:
        return BuiltinAgentName.AUTO_APPROVE, False
    return args.agent, args.auto_approve


def _session_intent(
    args: argparse.Namespace, *, allow_picker: bool
) -> LocalSessionIntent:
    from vibe.app_server.local import (
        ContinueSessionIntent,
        NewSessionIntent,
        ResumeSessionIntent,
    )

    if args.continue_session:
        return ContinueSessionIntent()
    if args.resume is True:
        if allow_picker:
            return NewSessionIntent()
        raise HeadlessUsageError("--resume requires a session ID in programmatic mode")
    if isinstance(args.resume, str):
        return ResumeSessionIntent(args.resume)
    return NewSessionIntent()


def _load_headless_config(run: HeadlessRun) -> ConfigOrchestrator[VibeConfigSchema]:
    try:
        orchestrator = load_config_orchestrator()
        orchestrator.config.require_active_provider_api_key()
    except ConfigLoadError as e:
        run.fail(RunOutcome.CONFIG_ERROR, e.message)
    except LayerImplementationError as e:
        # A config file that does not parse (bad TOML) surfaces here; a headless
        # run reports it as a config error rather than a crash.
        cause = f": {e.__cause__}" if e.__cause__ is not None else ""
        run.fail(RunOutcome.CONFIG_ERROR, f"Cannot load the Vibe config. {e}{cause}")
    except MissingAPIKeyError as e:
        run.fail(RunOutcome.CONFIG_ERROR, _missing_api_key_message(e))
    return orchestrator


def _headless_prompt(args: argparse.Namespace, run: HeadlessRun) -> str:
    from vibe.utils.io import read_safe

    if args.prompt_file is not None:
        if args.prompt:
            run.fail(RunOutcome.USAGE_ERROR, "Pass either -p TEXT or --prompt-file")
        try:
            prompt = read_safe(args.prompt_file, raise_on_error=True).text.strip()
        except (OSError, UnicodeDecodeError) as e:
            run.fail(
                RunOutcome.USAGE_ERROR,
                f"Cannot read the prompt file {args.prompt_file}: {e}",
            )
    else:
        prompt = args.prompt or get_prompt_from_stdin() or ""
    if not prompt:
        run.fail(RunOutcome.USAGE_ERROR, "No prompt provided for programmatic mode")
    return prompt


def connect_agent_socket_for_run(path: Path, run: HeadlessRun) -> AgentSocket:
    """Handshake with the ``--agent-socket`` server before the run starts.

    A server that aborts the run, at any point of it, stops the run as
    ``aborted``.
    """
    from mistralai_vibe_local_harness.vibe import SANDBOX_FAILURES
    from vibe.app_server._client_provided_tools import ClientToolDeclarationError
    from vibe.app_server._sandbox_workspace import warn_of_ignored_project_sources
    from vibe.cli.agent_socket import RunAbortedError, connect_agent_socket

    async def connect() -> AgentSocket:
        agent_socket = await connect_agent_socket(path, on_abort=run.stop.abort)
        if agent_socket.sandbox is not None:
            await warn_of_ignored_project_sources(agent_socket.sandbox)
        return agent_socket

    try:
        return asyncio.run(connect())
    except RunAbortedError as e:
        run.fail(RunOutcome.ABORTED, str(e))
    except ClientToolDeclarationError as e:
        run.fail(
            RunOutcome.USAGE_ERROR,
            f"The agent socket at {path} declares a tool Vibe cannot offer: {e}",
        )
    except SANDBOX_FAILURES as e:
        run.fail(
            RunOutcome.INFRASTRUCTURE_FAILURE,
            f"Cannot open the agent socket at {path}: {e}",
        )


def _run_programmatic_mode(args: argparse.Namespace, run: HeadlessRun) -> NoReturn:
    from vibe.app_server.local import ClientDescriptor, LocalHarnessOptions
    from vibe.app_server.protocol import (
        AppServerResponseError,
        ClientCapabilities,
        ClientInfo,
        SessionOptions,
    )
    from vibe.cli.programmatic import (
        OutputFormat,
        ProgrammaticTeleportError,
        is_usage_error,
        run_programmatic,
    )

    programmatic_prompt = _headless_prompt(args, run)
    output_format = OutputFormat(args.output if hasattr(args, "output") else "text")

    agent, auto_approve = _agent_selection(args)
    # The entrypoint connects first when it must know whether the socket
    # serves a sandbox.
    agent_socket: AgentSocket | None = args.agent_connection
    if agent_socket is None and args.agent_socket is not None:
        agent_socket = connect_agent_socket_for_run(args.agent_socket, run)
    sandbox = None if agent_socket is None else agent_socket.sandbox
    client_tools = (
        None if agent_socket is None else agent_socket.client_tools(args.time_limit)
    )
    try:
        session_intent = _session_intent(args, allow_picker=False)
        report = run_programmatic(
            harness_options=LocalHarnessOptions(
                experimental_harness=args.experimental_harness,
                legacy_harness=args.legacy_harness,
                client=ClientDescriptor(
                    info=ClientInfo(
                        name="vibe_programmatic",
                        title="Vibe programmatic CLI",
                        version=__version__,
                        entrypoint="programmatic",
                        terminal_emulator=detect_terminal(),
                    ),
                    capabilities=ClientCapabilities(
                        callback_kinds=["approval", "user_input"]
                    ),
                ),
                session_options=SessionOptions(
                    # A sandboxed session starts in the sandbox's workspace.
                    cwd=None if sandbox is not None else str(Path.cwd()),
                    workspace_roots=list(args.add_dir),
                    agent=agent,
                    auto_approve=auto_approve,
                    enabled_tools=args.enabled_tools,
                    disabled_tools=[
                        *(args.disabled_tools or ()),
                        "ask_user_question",
                        "exit_plan_mode",
                        # A headless session has no scheduler, so every call
                        # would fail.
                        "cron",
                    ],
                    # Token and price budgets are enforced by the run itself.
                    max_turns=args.max_turns,
                    headless=True,
                    # Trust is a host decision about a host directory.
                    trust_workspace=sandbox is None
                    and bool(args.trust or args.worktree),
                ),
                session=session_intent,
                sandbox=sandbox,
                client_tools=client_tools,
                project_instructions=None
                if agent_socket is None
                else agent_socket.instructions,
            ),
            prompt=programmatic_prompt,
            output_format=output_format,
            teleport=args.teleport,
            stop=run.stop,
        )
    except ProgrammaticTeleportError as e:
        run.fail(RunOutcome.USAGE_ERROR, f"Teleport error: {e}")
    except AppServerResponseError as e:
        outcome = (
            RunOutcome.USAGE_ERROR
            if is_usage_error(e)
            else RunOutcome.INFRASTRUCTURE_FAILURE
        )
        run.fail(outcome, e.error.message)
    except HeadlessUsageError as e:
        run.fail(RunOutcome.USAGE_ERROR, str(e))
    except ExperimentalHarnessUnavailableError as e:
        # The Unified Harness is the required default runtime: startup aborts
        # here with an actionable message instead of falling back to legacy.
        run.fail(RunOutcome.INFRASTRUCTURE_FAILURE, str(e))
    except Exception as e:
        # The outermost boundary of a headless run: whatever escaped still
        # leaves an export and exit 2, never a traceback without one.
        logger.exception("Programmatic run failed")
        run.fail(RunOutcome.INFRASTRUCTURE_FAILURE, str(e) or type(e).__name__)
    if report.final_response:
        print(report.final_response)
    result = report.result
    if result.error is not None:
        print(f"Error: {result.error.message}", file=sys.stderr)
    elif result.outcome is not RunOutcome.FINISHED:
        print(f"Stopped: {result.outcome}", file=sys.stderr)
    run.exit(report)


def _run_interactive_mode(
    args: argparse.Namespace,
    stdin_prompt: str | None,
    update_cache_repository: UpdateCacheRepository,
    *,
    autocopy_to_clipboard: bool,
) -> None:
    from vibe.app_server.local import (
        ClientDescriptor,
        LocalHarness,
        LocalHarnessOptions,
    )
    from vibe.app_server.protocol import (
        AppServerResponseError,
        ClientCapabilities,
        ClientInfo,
        SessionOptions,
    )
    from vibe.cli.textual_ui.app import StartupOptions, run_textual_ui

    # --worktree runs in a checkout Vibe just made from the repo the user
    # launched from, and --trust is the user saying so outright. Both grant the
    # workspace session trust when the session is built, so prompting first
    # would ask about a decision already taken - and for --worktree it would
    # ask again for every new worktree, which is one per session.
    trust_workspace = bool(args.trust or args.worktree)

    agent, auto_approve = _agent_selection(args)

    harness = LocalHarness(
        LocalHarnessOptions(
            experimental_harness=args.experimental_harness,
            legacy_harness=args.legacy_harness,
            client=ClientDescriptor(
                info=ClientInfo(
                    name="vibe_tui",
                    title="Vibe Textual",
                    version=__version__,
                    entrypoint="cli",
                    terminal_emulator=detect_terminal(),
                ),
                capabilities=ClientCapabilities(
                    callback_kinds=["approval", "user_input"]
                ),
            ),
            session_options=SessionOptions(
                cwd=str(Path.cwd()),
                workspace_roots=list(args.add_dir),
                agent=agent,
                auto_approve=auto_approve,
                enabled_tools=args.enabled_tools,
                disabled_tools=list(args.disabled_tools or ()),
                trust_workspace=trust_workspace,
            ),
            session=_session_intent(args, allow_picker=True),
        )
    )
    try:
        summary = run_textual_ui(
            start_app_server=harness.connect,
            history_file=HISTORY_FILE.path,
            update_cache_repository=update_cache_repository,
            startup=StartupOptions(
                initial_prompt=args.initial_prompt or stdin_prompt,
                teleport_on_start=args.teleport,
                show_resume_picker=args.resume is True,
                is_resuming_session=(
                    args.continue_session or isinstance(args.resume, str)
                ),
                prompt_for_workspace_trust=not trust_workspace,
                autocopy_to_clipboard=autocopy_to_clipboard,
                resume_session_id=(
                    args.resume if isinstance(args.resume, str) else None
                ),
                continue_latest=bool(args.continue_session),
            ),
        )
    except AppServerResponseError as exc:
        rprint(f"[red]Error:[/] {exc.error.message}")
        sys.exit(1)
    except ExperimentalHarnessUnavailableError as e:
        # The Unified Harness is the required default runtime: startup aborts
        # here with an actionable message instead of falling back to legacy.
        # The reason text is escaped: an exception message containing square
        # brackets would otherwise be read as rich markup.
        rprint(f"[red]Error:[/] {escape(str(e))}")
        sys.exit(1)
    print_session_resume_message(summary)


def _show_update_prompt(
    repository: UpdateCacheRepository,
    latest_version: str,
    *,
    theme: str | None,
    dismiss_on_continue: bool,
    prompt_mode: UpdatePromptMode,
) -> None:
    from vibe.setup.update_prompt import UpdatePromptResult, ask_update_prompt

    result = ask_update_prompt(
        __version__, latest_version, theme=theme, prompt_mode=prompt_mode
    )

    match result:
        case UpdatePromptResult.CONTINUE:
            if dismiss_on_continue:
                try:
                    asyncio.run(mark_update_as_dismissed(repository, latest_version))
                except OSError as exc:
                    logger.debug("Failed to persist dismissed update", exc_info=exc)
            return
        case UpdatePromptResult.QUIT:
            sys.exit(0)
        case UpdatePromptResult.UPDATED:
            _print_update_succeeded(latest_version)
            sys.exit(0)
        case UpdatePromptResult.UPDATE_FAILED:
            if _confirm_force_reinstall():
                rprint("Reinstalling mistral-vibe…")
                if asyncio.run(force_reinstall_latest(latest_version)):
                    _print_update_succeeded(latest_version)
                    sys.exit(0)
            rprint(
                "[yellow]Vibe could not update automatically.[/]\n"
                "  Update manually with your package manager (for example "
                "[bold]uv tool upgrade mistral-vibe[/]), or keep using "
                f"the current version ({__version__}) for now."
            )
            sys.exit(1)


def _print_update_succeeded(latest_version: str) -> None:
    rprint(
        f"[green]✔ Vibe was updated from {__version__} to "
        f"{latest_version}.[/]\n  Run [bold]vibe[/] to start using the "
        "new version."
    )


def _confirm_force_reinstall() -> bool:
    if not is_uv_tool_install():
        return False
    rprint(
        "[yellow]The update didn't apply, your uv install is likely pinned.[/]\n"
        f"  [bold]{FORCE_REINSTALL_COMMAND}[/] installs the latest version "
        "but drops the pin, extras and --with packages."
    )
    sys.stdout.write("Run it now? [y/N] ")
    sys.stdout.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        return False
    return answer in {"y", "yes"}


def _maybe_run_startup_update_prompt(
    config: VibeConfigSchema, repository: UpdateCacheRepository
) -> None:
    if not config.enable_update_checks:
        return

    try:
        latest_version = asyncio.run(
            get_pending_update_from_cache(repository, __version__)
        )
    except OSError as exc:
        logger.debug("Failed to read pending update from cache", exc_info=exc)
        return

    if latest_version is None:
        return

    from vibe.setup.update_prompt import UpdatePromptMode

    _show_update_prompt(
        repository,
        latest_version,
        theme=config.theme,
        dismiss_on_continue=True,
        prompt_mode=UpdatePromptMode.STARTUP,
    )


def _run_check_upgrade(
    repository: UpdateCacheRepository,
    *,
    update_notifier: UpdateGateway | None = None,
    theme: str | None = None,
) -> None:
    from vibe.setup.update_prompt import UpdatePromptMode

    notifier = update_notifier or create_update_gateway("mistral-vibe")
    try:
        update = asyncio.run(
            get_update_if_available(
                update_notifier=notifier,
                current_version=__version__,
                update_cache_repository=repository,
                force_check=True,
            )
        )
    except UpdateError as exc:
        rprint(f"[red]✗ Update check failed:[/] {exc.message}")
        sys.exit(1)
    except OSError as exc:
        logger.debug("Failed to persist forced update check", exc_info=exc)
        rprint("[red]✗ Update check failed while writing the update cache.[/]")
        sys.exit(1)

    if update is None:
        rprint(f"[green]Vibe is already up to date ({__version__}).[/]")
        return

    _show_update_prompt(
        repository,
        update.latest_version,
        theme=theme,
        dismiss_on_continue=False,
        prompt_mode=UpdatePromptMode.CHECK_UPGRADE,
    )


def run_cli(args: argparse.Namespace, *, headless: HeadlessRun | None) -> None:
    sentry_enabled = False

    load_dotenv_values()
    bootstrap_vibe_home()

    if args.setup:
        from vibe.setup.onboarding import run_onboarding

        orchestrator = load_config_orchestrator_or_exit()
        run_onboarding(
            launch_context=_build_cli_launch_context(), orchestrator=orchestrator
        )
        sys.exit(0)

    try:
        update_cache_repository = FileSystemUpdateCacheRepository()
        if getattr(args, "check_upgrade", False):
            from vibe.cli.theme import resolve_theme_name

            config = load_config_orchestrator_or_exit().config
            _run_check_upgrade(
                update_cache_repository, theme=resolve_theme_name(config.theme)
            )
            sys.exit(0)

        if (run := headless) is not None:
            with run.stop.handling_stop_signals():
                try:
                    config = _load_headless_config(run).config
                    sentry_enabled = init_sentry(
                        enabled=config.enable_telemetry,
                        headless=True,
                        tags=_build_cli_launch_context().sentry_tags(),
                    )
                    _run_programmatic_mode(args, run)
                except RunTerminated:
                    run.exit(RunReport(result=RunResult(outcome=RunOutcome.TERMINATED)))

        orchestrator = require_api_key_or_onboard(load_config_orchestrator_or_exit())
        config = orchestrator.config
        _maybe_run_startup_update_prompt(config, update_cache_repository)
        sentry_enabled = init_sentry(
            enabled=config.enable_telemetry,
            headless=False,
            tags=_build_cli_launch_context().sentry_tags(),
        )
        _run_interactive_mode(
            args=args,
            stdin_prompt=get_prompt_from_stdin(),
            update_cache_repository=update_cache_repository,
            autocopy_to_clipboard=config.autocopy_to_clipboard,
        )

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]Bye![/]")
        sys.exit(0)
    finally:
        if sentry_enabled:
            import sentry_sdk

            if sentry_sdk.is_initialized():
                sentry_sdk.flush(timeout=5)
