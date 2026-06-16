from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from pydantic import ValidationError
from rich import print as rprint
from rich.console import Console
import tomli_w

from vibe import __version__
from vibe.cli.terminal_detect import detect_terminal
from vibe.cli.textual_ui.app import StartupOptions, run_textual_ui
from vibe.cli.update_notifier import (
    FileSystemUpdateCacheRepository,
    UpdateCacheRepository,
    get_pending_update_from_cache,
    mark_update_as_dismissed,
)
from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import MissingAPIKeyError, VibeConfig, load_dotenv_values
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.hooks.config import HookConfigResult, load_hooks_from_fs
from vibe.core.logger import logger
from vibe.core.paths import HISTORY_FILE
from vibe.core.programmatic import run_programmatic
from vibe.core.session import last_session_pointer
from vibe.core.session.session_loader import SessionLoader
from vibe.core.telemetry.build_metadata import build_entrypoint_metadata
from vibe.core.telemetry.types import EntrypointMetadata
from vibe.core.tracing import setup_tracing
from vibe.core.trusted_folders import find_trustable_files, trusted_folders_manager
from vibe.core.types import LLMMessage, OutputFormat, Role
from vibe.core.utils import ConversationLimitException
from vibe.setup.onboarding import run_onboarding
from vibe.setup.update_prompt import UpdatePromptResult, ask_update_prompt


def _build_cli_entrypoint_metadata() -> EntrypointMetadata:
    return build_entrypoint_metadata(
        agent_entrypoint="cli",
        agent_version=__version__,
        client_name="vibe_cli",
        client_version=__version__,
    )


def get_initial_agent_name(args: argparse.Namespace, config: VibeConfig) -> str:
    if args.auto_approve:
        return BuiltinAgentName.AUTO_APPROVE

    return args.agent or config.default_agent


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


def _format_config_validation_error(exc: ValidationError) -> str:
    lines = [f"Invalid configuration ({exc.error_count()} error(s)):"]
    for err in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config_or_exit(*, interactive: bool) -> VibeConfig:
    try:
        return VibeConfig.load()
    except MissingAPIKeyError as e:
        if not interactive:
            print(
                f"Error: {e}. Set the environment variable (e.g. in ~/.vibe/.env "
                "or your shell), or run `vibe --setup` once interactively.",
                file=sys.stderr,
            )
            sys.exit(1)
        run_onboarding(entrypoint_metadata=_build_cli_entrypoint_metadata())
        return VibeConfig.load()
    except ValidationError as e:
        rprint(f"[yellow]{_format_config_validation_error(e)}[/]")
        sys.exit(1)
    except ValueError as e:
        rprint(f"[yellow]{e}[/]")
        sys.exit(1)


def warn_if_workdir_trust_is_unset() -> None:
    try:
        cwd = Path.cwd()
    except FileNotFoundError:
        return
    if cwd.resolve() == Path.home().resolve():
        return
    if trusted_folders_manager.is_trusted(cwd) is not None:
        return
    detected = find_trustable_files(cwd)
    if not detected:
        return
    files_str = ", ".join(detected)
    Console(stderr=True).print(
        f"[yellow]Warning:[/] {cwd} is not trusted; "
        f"project configuration ({files_str}) will be ignored. "
        "Re-run with --trust to trust this folder temporarily."
    )


def bootstrap_config_files() -> None:
    mgr = get_harness_files_manager()
    config_file = mgr.user_config_file
    if not config_file.exists():
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with config_file.open("wb") as f:
                tomli_w.dump(VibeConfig.create_default(), f)
        except Exception as e:
            rprint(f"[yellow]Could not create default config file: {e}[/]")

    history_file = HISTORY_FILE.path
    if not history_file.exists():
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text("Hello Vibe!\n", "utf-8")
        except Exception as e:
            rprint(f"[yellow]Could not create history file: {e}[/]")


def load_session(
    args: argparse.Namespace, config: VibeConfig
) -> tuple[list[LLMMessage], Path] | None:
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
        cwd = Path.cwd().resolve()
        pointer_session_id = last_session_pointer.load(config.session_logging)
        if pointer_session_id:
            session_to_load = SessionLoader.find_session_by_id(
                pointer_session_id, config.session_logging, working_directory=cwd
            )
        if not session_to_load:
            session_to_load = SessionLoader.find_latest_session(
                config.session_logging, working_directory=cwd
            )
        if not session_to_load:
            rprint(
                f"[red]No previous sessions found in "
                f"{config.session_logging.save_dir} for {cwd=}[/]"
            )
            sys.exit(1)
    elif args.resume is True:
        return None
    else:
        session_to_load = SessionLoader.find_session_by_id(
            args.resume, config.session_logging
        )
        if not session_to_load:
            rprint(
                f"[red]Session '{args.resume}' not found in "
                f"{config.session_logging.save_dir}[/]"
            )
            sys.exit(1)

    try:
        loaded_messages, _ = SessionLoader.load_session(session_to_load)
        return loaded_messages, session_to_load
    except Exception as e:
        rprint(f"[red]Failed to load session: {e}[/]")
        sys.exit(1)


def _resume_previous_session(
    agent_loop: AgentLoop, loaded_messages: list[LLMMessage], session_path: Path
) -> None:
    non_system_messages = [msg for msg in loaded_messages if msg.role != Role.system]
    agent_loop.messages.extend(non_system_messages)

    _, metadata = SessionLoader.load_session(session_path)
    session_id = metadata.get("session_id", agent_loop.session_id)
    agent_loop.session_id = session_id
    agent_loop.parent_session_id = metadata.get("parent_session_id")
    agent_loop.session_logger.resume_existing_session(session_id, session_path)

    logger.info(
        "Resumed session %s with %d messages", session_id, len(non_system_messages)
    )


def _run_programmatic_mode(
    args: argparse.Namespace,
    config: VibeConfig,
    initial_agent_name: str,
    hook_config_result: HookConfigResult,
    loaded_session: tuple[list[LLMMessage], Path] | None,
    stdin_prompt: str | None,
) -> None:
    warn_if_workdir_trust_is_unset()
    config.disabled_tools = [
        *config.disabled_tools,
        "ask_user_question",
        "exit_plan_mode",
    ]
    programmatic_prompt = args.prompt or stdin_prompt
    if not programmatic_prompt:
        print("Error: No prompt provided for programmatic mode", file=sys.stderr)
        sys.exit(1)
    output_format = OutputFormat(args.output if hasattr(args, "output") else "text")

    try:
        final_response = run_programmatic(
            config=config,
            prompt=programmatic_prompt or "",
            max_turns=args.max_turns,
            max_price=args.max_price,
            max_session_tokens=args.max_tokens,
            output_format=output_format,
            previous_messages=loaded_session[0] if loaded_session else None,
            agent_name=initial_agent_name,
            teleport=args.teleport and config.vibe_code_enabled,
            headless=True,
            hook_config_result=hook_config_result,
        )
        if final_response:
            print(final_response)
        sys.exit(0)
    except ConversationLimitException as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    except TeleportError as e:
        print(f"Teleport error: {e}", file=sys.stderr)
        sys.exit(1)
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _maybe_run_startup_update_prompt(
    config: VibeConfig, repository: UpdateCacheRepository
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

    result = ask_update_prompt(__version__, latest_version, theme=config.theme)

    match result:
        case UpdatePromptResult.CONTINUE:
            try:
                asyncio.run(mark_update_as_dismissed(repository, latest_version))
            except OSError as exc:
                logger.debug("Failed to persist dismissed update", exc_info=exc)
            return
        case UpdatePromptResult.QUIT:
            sys.exit(0)
        case UpdatePromptResult.UPDATED:
            rprint(
                f"[green]✔ Vibe was updated from {__version__} to "
                f"{latest_version}.[/]\n  Run [bold]vibe[/] to start using the "
                "new version."
            )
            sys.exit(0)
        case UpdatePromptResult.UPDATE_FAILED:
            rprint(
                "[yellow]Vibe could not update automatically.[/]\n"
                "  Update manually with your package manager (for example "
                "[bold]uv tool upgrade mistral-vibe[/]), or keep using "
                f"the current version ({__version__}) for now."
            )
            sys.exit(1)


def run_cli(args: argparse.Namespace) -> None:
    load_dotenv_values()
    bootstrap_config_files()

    if args.setup:
        run_onboarding(entrypoint_metadata=_build_cli_entrypoint_metadata())
        sys.exit(0)

    try:
        is_interactive = args.prompt is None
        config = load_config_or_exit(interactive=is_interactive)
        update_cache_repository = FileSystemUpdateCacheRepository()

        if is_interactive:
            _maybe_run_startup_update_prompt(config, update_cache_repository)

        initial_agent_name = get_initial_agent_name(args, config)
        hook_config_result = load_hooks_from_fs(config)
        setup_tracing(config)

        if args.enabled_tools:
            config.enabled_tools = args.enabled_tools

        loaded_session = load_session(args, config)

        stdin_prompt = get_prompt_from_stdin()
        if is_interactive:
            try:
                agent_loop = AgentLoop(
                    config,
                    agent_name=initial_agent_name,
                    enable_streaming=True,
                    entrypoint_metadata=_build_cli_entrypoint_metadata(),
                    terminal_emulator=detect_terminal(),
                    defer_heavy_init=True,
                    hook_config_result=hook_config_result,
                )
            except ValueError as e:
                rprint(f"[red]Error:[/] {e}")
                sys.exit(1)

            if loaded_session:
                _resume_previous_session(agent_loop, *loaded_session)

            run_textual_ui(
                agent_loop=agent_loop,
                update_cache_repository=update_cache_repository,
                startup=StartupOptions(
                    initial_prompt=args.initial_prompt or stdin_prompt,
                    teleport_on_start=args.teleport,
                    show_resume_picker=args.resume is True,
                    is_resuming_session=loaded_session is not None,
                ),
            )
        else:
            _run_programmatic_mode(
                args=args,
                config=config,
                initial_agent_name=initial_agent_name,
                hook_config_result=hook_config_result,
                loaded_session=loaded_session,
                stdin_prompt=stdin_prompt,
            )

    except (KeyboardInterrupt, EOFError):
        rprint("\n[dim]Bye![/]")
        sys.exit(0)
