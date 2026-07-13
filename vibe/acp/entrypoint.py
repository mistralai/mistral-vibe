from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys

import tomli_w

from vibe import __version__
from vibe.core.config import VibeConfig
from vibe.core.config.harness_files import (
    get_harness_files_manager,
    init_harness_files_manager,
)
from vibe.core.logger import logger
from vibe.core.paths import HISTORY_FILE
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.utils.windows_asyncio import silence_proactor_transport_teardown_warnings

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
    mgr = get_harness_files_manager()
    config_file = mgr.user_config_file
    if not config_file.exists():
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with config_file.open("wb") as f:
                tomli_w.dump(VibeConfig.create_default(), f)
        except Exception as e:
            logger.error(f"Could not create default config file: {e}")
            raise

    history_file = HISTORY_FILE.path
    if not history_file.exists():
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text("Hello Vibe!\n", "utf-8")
        except Exception as e:
            logger.error(f"Could not create history file: {e}")
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

    from vibe.acp.acp_agent_loop import run_acp_server
    from vibe.core.config import VibeConfig, load_dotenv_values
    from vibe.core.sentry import SentryTarget, init_sentry
    from vibe.core.tracing import setup_tracing
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
        config = VibeConfig.load()
    except Exception:
        config = None

    if config is not None:
        try:
            setup_tracing(config)
        except Exception:
            pass  # tracing disabled
        try:
            init_sentry(
                config,
                headless=True,
                launch_context=build_launch_context(
                    agent_entrypoint="acp",
                    agent_version=__version__,
                    client_name="vibe_acp",
                    client_version=__version__,
                ),
                target=SentryTarget.ACP,
            )
        except Exception:
            pass  # error reporting disabled

    run_acp_server(environ_before_dotenv_load=environ_before_dotenv_load)


if __name__ == "__main__":
    main()
