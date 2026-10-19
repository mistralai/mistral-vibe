from __future__ import annotations

import subprocess
import sys

import pytest

_SESSION_START_MODULES = (
    "vibe.app_server.stdio",
    "vibe.app_server._mcp_auth",
    "vibe.app_server.mcp_catalog",
    "vibe.app_server._unified_harness_backend_adapter",
    "vibe.core.tools.mcp.registry",
    "mistralai_vibe_local_harness.vibe",
)


@pytest.mark.timeout(60)
def test_session_start_modules_do_not_import_the_mcp_sdk() -> None:
    imports = "; ".join(f"import {module}" for module in _SESSION_START_MODULES)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; {imports}; "
            "assert 'mcp' not in sys.modules, 'MCP SDK imported at startup'",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{result.stderr}\nIf harness sources changed, run `uv sync` to restage them."
    )
