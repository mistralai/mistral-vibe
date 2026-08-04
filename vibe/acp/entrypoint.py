from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import os
import sys

from vibe import __version__
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import init_harness_files_manager
from vibe.core.paths import HISTORY_FILE, LOG_FILE
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.utils.windows_asyncio import silence_proactor_transport_teardown_warnings
from vibe.observability.logging import init_file_logging, logger

# Configure line buffering for subprocess communication
sys.stdout.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
sys.stderr.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
sys.stdin.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]


@dataclass
class Arguments:
    setup: bool


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(description="Run Mistral Vibe in ACP mode")
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--setup", action="store_true", help="Setup API key and exit")
    args = parser.parse_args()
    return Arguments(setup=args.setup)


def bootstrap_config_files() -> None:
    history_file = HISTORY_FILE.path
    if not history_file.exists():
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text("Hello Vibe!\n", "utf-8")
        except Exception as e:
            logger.error("Could not create history file: %s", e)
            raise


# When DEBUG_MODE=true, attaches debugpy on localhost:5678.
def handle_debug_mode() -> None:
    if os.environ.get("DEBUG_MODE") != "true":
        return

    try:
        import debugpy
    except ImportError:
        return

    debugpy.listen(("localhost", 5678))
    # uncomment this to wait for the debugger to attach
    # debugpy.wait_for_client()


def main() -> None:
    silence_proactor_transport_teardown_warnings()

    handle_debug_mode()
    init_harness_files_manager("user", "project")
    init_file_logging(LOG_FILE.path)

    from vibe.acp.agent import run_acp_server
    from vibe.core.config import load_dotenv_values
    from vibe.observability.sentry import SentryTarget, init_sentry
    from vibe.setup.onboarding import run_onboarding

    environ_before_dotenv_load = os.environ.copy()
    load_dotenv_values()
    bootstrap_config_files()
    args = parse_arguments()
    if args.setup:
        run_onboarding(
            launch_context=build_launch_context(
                agent_entrypoint="acp",
                agent_version=__version__,
                client_name="vibe_acp",
                client_version=__version__,
            )
        )
        sys.exit(0)

    try:
        orchestrator = asyncio.run(build_default_orchestrator())
        config = orchestrator.config
    except Exception:
        config = None

    if config is not None:
        try:
            init_sentry(
                enabled=config.enable_telemetry,
                headless=True,
                tags=build_launch_context(
                    agent_entrypoint="acp",
                    agent_version=__version__,
                    client_name="vibe_acp",
                    client_version=__version__,
                ).sentry_tags(),
                target=SentryTarget.ACP,
            )
        except Exception:
            pass  # error reporting disabled

    run_acp_server(environ_before_dotenv_load=environ_before_dotenv_load)


if __name__ == "__main__":
    main()
