from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import os
import sys

from vibe import __version__
from vibe._experimental_harness import (
    ExperimentalHarnessUnavailableError,
    require_experimental_harness,
)
from vibe.cli.launcher import exec_rust_cli
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import init_harness_files_manager
from vibe.core.paths import LOG_FILE, bootstrap_vibe_home
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.utils.windows_asyncio import silence_proactor_transport_teardown_warnings
from vibe.observability.logging import init_file_logging

# Read by vibe-rs; an env var rather than a flag so an older vibe-rs ignores it.
SETUP_ENTRYPOINT_ENV = "VIBE_SETUP_ENTRYPOINT"
# Hidden mode the bundled vibe-rs spawns for `--setup`: only setup/* RPCs work, the
# bundle lacks vibe-app-server's PTY helper, built-in skills and backend adapter.
SETUP_APP_SERVER_FLAG = "--setup-app-server"


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


def _run_setup() -> None:
    # A frozen bundle ships no vibe-app-server: vibe-rs spawns this executable.
    frozen = getattr(sys, "frozen", False)
    exec_rust_cli(
        ["--setup"],
        extra_env={SETUP_ENTRYPOINT_ENV: "acp"},
        app_server_cmd=[sys.executable, SETUP_APP_SERVER_FLAG] if frozen else None,
    )


def main() -> None:
    if sys.argv[1:2] == [SETUP_APP_SERVER_FLAG]:
        from vibe.app_server.stdio import main as app_server_main

        app_server_main(sys.argv[2:])
        return
    args = parse_arguments()
    if args.setup:
        # Before the dotenv load: vibe-rs must tell ~/.vibe/.env keys from real env.
        _run_setup()
        return

    # Configure line buffering for subprocess communication
    sys.stdout.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
    sys.stderr.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
    sys.stdin.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
    silence_proactor_transport_teardown_warnings()

    # The gate must run before the harness files manager and file logging:
    # their mkdir(parents=True) calls are otherwise the first to materialize
    # ~/.vibe, at permissive modes.
    bootstrap_vibe_home()
    init_harness_files_manager("user", "project")
    init_file_logging(LOG_FILE.path)

    from vibe.acp.agent import run_acp_server
    from vibe.core.config import load_dotenv_values
    from vibe.observability.sentry import init_sentry

    environ_before_dotenv_load = os.environ.copy()
    load_dotenv_values()

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
            )
        except Exception:
            pass  # error reporting disabled

    # The Unified Harness is the required default runtime. Fail before the
    # ACP server starts serving: a missing or incompatible Runtime must abort
    # startup with an actionable error, not surface as a per-session failure
    # on the live protocol.
    try:
        require_experimental_harness()
        run_acp_server(environ_before_dotenv_load=environ_before_dotenv_load)
    except ExperimentalHarnessUnavailableError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
