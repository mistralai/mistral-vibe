from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import os
import sys

from vibe import __version__
from vibe._experimental_harness import add_experimental_harness_argument
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.harness_files import init_harness_files_manager
from vibe.core.paths import LOG_FILE, bootstrap_vibe_home
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.utils.windows_asyncio import silence_proactor_transport_teardown_warnings
from vibe.observability.logging import init_file_logging

# Configure line buffering for subprocess communication
sys.stdout.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
sys.stderr.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
sys.stdin.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]


@dataclass
class Arguments:
    setup: bool
    experimental_harness: bool
    legacy_harness: bool


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(description="Run Mistral Vibe in ACP mode")
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--setup", action="store_true", help="Setup API key and exit")
    harness_group = parser.add_mutually_exclusive_group()
    add_experimental_harness_argument(parser, group=harness_group)
    harness_group.add_argument(
        "--legacy-harness",
        action="store_true",
        default=False,
        help="Force the legacy Python harness, overriding the GrowthBook rollout.",
    )
    args = parser.parse_args()
    return Arguments(
        setup=args.setup,
        experimental_harness=args.experimental_harness,
        legacy_harness=args.legacy_harness,
    )


def main() -> None:
    silence_proactor_transport_teardown_warnings()

    # The gate must run before the harness files manager and file logging:
    # their mkdir(parents=True) calls are otherwise the first to materialize
    # ~/.vibe, at permissive modes.
    bootstrap_vibe_home()
    init_harness_files_manager("user", "project")
    init_file_logging(LOG_FILE.path)

    from vibe.acp.agent import run_acp_server
    from vibe.core.config import load_dotenv_values
    from vibe.observability.sentry import SentryTarget, init_sentry
    from vibe.setup.onboarding import run_onboarding

    environ_before_dotenv_load = os.environ.copy()
    load_dotenv_values()
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

    run_acp_server(
        environ_before_dotenv_load=environ_before_dotenv_load,
        experimental_harness=args.experimental_harness,
        legacy_harness=args.legacy_harness,
    )


if __name__ == "__main__":
    main()
