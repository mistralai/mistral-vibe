from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys

from vibe.acp.acp_agent import run_acp_server
from vibe.setup.onboarding import run_onboarding

# Configure line buffering for subprocess communication
sys.stdout.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
sys.stderr.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]
sys.stdin.reconfigure(line_buffering=True)  # pyright: ignore[reportAttributeAccessIssue]


@dataclass
class Arguments:
    setup: bool


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(description="Run Mistral Vibe in ACP mode")
    parser.add_argument("--setup", action="store_true", help="Setup API key and exit")
    args = parser.parse_args()
    return Arguments(setup=args.setup)


def main() -> None:
    args = parse_arguments()
    if args.setup:
        run_onboarding()
        sys.exit(0)
    run_acp_server()


if __name__ == "__main__":
    main()
