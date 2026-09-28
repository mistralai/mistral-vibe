from __future__ import annotations

import os
import sys

# Kept intentionally tiny: the `vibe` console script lands here, so a Rust
# invocation must reach the Rust binary without importing the Python CLI stack.
#
# The implementation (Rust vs. Python) is chosen by the undocumented `VIBE_CLI`
# environment variable:
#   VIBE_CLI=rust    -> launch the Rust TUI
#   VIBE_CLI=python  -> force the Python TUI
# Anything else (including unset) falls through to the Python TUI.


def main() -> None:
    args = sys.argv[1:]
    option_args = args[: args.index("--")] if "--" in args else args
    python_only = (
        args[:1] in (["--internal-posix-pty-helper"], ["update"])
        or "--check-upgrade" in option_args
    )
    if not python_only and os.environ.get("VIBE_CLI", "").strip().lower() == "rust":
        from vibe.cli._rust import exec_rust_cli

        exec_rust_cli(args)

    # Importing entrypoint also runs its module-top pty-helper check.
    from vibe.cli.entrypoint import main as entrypoint_main

    entrypoint_main()
