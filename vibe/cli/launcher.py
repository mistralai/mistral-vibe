from __future__ import annotations

import os
import sys

# Kept intentionally tiny: the `vibe` console script lands here, so a Rust
# invocation must reach the Rust binary without importing the Python CLI stack.
#
# The implementation (Rust vs. Python) is chosen by the `VIBE_CLI` environment
# variable (documented in the README "TUI Implementations" section):
#   VIBE_CLI=rust    -> launch the Rust TUI
#   VIBE_CLI=python  -> force the Python TUI
# Anything else (including unset) follows the cached GrowthBook
# `vibe_cli_rust_tui_rollout` variant, defaulting to the Python TUI.


def main() -> None:
    args = sys.argv[1:]
    option_args = args[: args.index("--")] if "--" in args else args
    python_only = args[:1] == ["--internal-posix-pty-helper"]
    selector = os.environ.get("VIBE_CLI", "").strip().lower()
    if not python_only and selector == "rust":
        from vibe.cli._rust import exec_rust_cli

        exec_rust_cli(args)
    elif not python_only and selector != "python":
        from vibe.cli._rust import exec_rust_cli, rust_rollout_selected

        if rust_rollout_selected(option_args):
            exec_rust_cli(args, rollout=True)

    # Importing entrypoint also runs its module-top pty-helper check.
    from vibe.cli.entrypoint import main as entrypoint_main

    entrypoint_main()
