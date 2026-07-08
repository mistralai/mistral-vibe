from __future__ import annotations

import subprocess
import sys


def test_cli_startup_does_not_import_deferred_heavy_modules() -> None:
    # These modules are expensive or only needed in one launch mode; importing
    # the CLI should leave them out until the first matching runtime path.
    code = """
import sys
import vibe.cli.entrypoint
import vibe.cli.cli

blocked_prefixes = [
    "mistralai",
    "opentelemetry",
    "vibe.core.llm.backend.mistral",
    "vibe.core.llm.backend.generic",
    "vibe.core.programmatic",
    "vibe.cli.textual_ui.app",
    "vibe.setup.onboarding",
    "vibe.setup.update_prompt",
]
loaded = [
    name
    for name in sys.modules
    if any(name == p or name.startswith(p + ".") for p in blocked_prefixes)
]
if loaded:
    raise SystemExit(f"unexpected heavy modules loaded at startup: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_entrypoint_module_import_stays_light() -> None:
    # --help/--version only run entrypoint's module level plus argparse, so
    # importing the module must not pull in the config stack.
    code = """
import sys
import vibe.cli.entrypoint

blocked_prefixes = ["pydantic", "rich", "textual", "vibe.core.config"]
loaded = [
    name
    for name in sys.modules
    if any(name == p or name.startswith(p + ".") for p in blocked_prefixes)
]
if loaded:
    raise SystemExit(f"unexpected heavy modules loaded by entrypoint: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout
