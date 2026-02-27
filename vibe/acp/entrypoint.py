from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import sys

from vibe import __version__
from vibe.core.config import VibeConfig
from vibe.core.logger import logger
from vibe.core.paths.config_paths import CONFIG_FILE, HISTORY_FILE, unlock_config_paths

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
    if not CONFIG_FILE.path.exists():
        try:
            VibeConfig.save_updates(VibeConfig.create_default())
        except Exception as e:
            logger.error(f"Could not create default config file: {e}")
            raise

    if not HISTORY_FILE.path.exists():
        try:
            HISTORY_FILE.path.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.path.write_text("Hello Vibe!\n", "utf-8")
        except Exception as e:
            logger.error(f"Could not create history file: {e}")
            raise


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
    handle_debug_mode()
    unlock_config_paths()

    from vibe.acp.acp_agent_loop import run_acp_server
    from vibe.setup.onboarding import run_onboarding

    bootstrap_config_files()
    args = parse_arguments()
    if args.setup:
        run_onboarding()
        sys.exit(0)
    run_acp_server()


if __name__ == "__main__":
    main()
