from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_importing_tui_app_does_not_import_deferred_startup_modules() -> None:
    code = """
import sys
import vibe.cli.textual_ui.app

blocked = [
    "vibe.cli.textual_ui.widgets.connector_auth_app",
    "vibe.cli.textual_ui.widgets.mcp_app",
    "vibe.core.agent_loop",
    "vibe.core.tools.connectors.connector_registry",
    "vibe.core.tools.mcp.tools",
    "mcp",
    "git",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected startup modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_importing_agent_loop_does_not_import_remote_tool_modules() -> None:
    code = """
import sys
import vibe.core.agent_loop

blocked = [
    "vibe.core.tools.connectors.connector_registry",
    "vibe.core.tools.mcp.tools",
    "vibe.core.teleport.git",
    "vibe.core.teleport.teleport",
    "mcp",
    "git",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected agent loop modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_importing_connector_registry_does_not_import_mcp_runtime() -> None:
    code = """
import sys
import vibe.core.tools.connectors.connector_registry

blocked = [
    "vibe.core.tools.mcp.tools",
    "mcp",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected connector registry modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_importing_mcp_app_does_not_import_mcp_runtime() -> None:
    code = """
import sys
import vibe.cli.textual_ui.widgets.mcp_app

blocked = [
    "vibe.core.tools.mcp.tools",
    "mcp",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected mcp app modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_constructing_deferred_agent_loop_does_not_import_mcp_package(
    tmp_path: Path,
) -> None:
    code = """
import sys

from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)


class Backend:
    async def complete(self, **kwargs):
        raise AssertionError

    async def __aexit__(self, *args):
        return None


init_harness_files_manager("user", "project")
try:
    config = VibeConfig(
        enable_connectors=False,
        session_logging=SessionLoggingConfig(enabled=False),
    )
    loop = AgentLoop(
        config=config,
        backend=Backend(),
        defer_heavy_init=True,
        headless=True,
    )
    if loop._deferred_init_thread is not None:
        loop._deferred_init_thread.join()
finally:
    reset_harness_files_manager()

blocked = [
    "vibe.core.tools.mcp.tools",
    "mcp",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected deferred agent loop modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "VIBE_HOME": str(tmp_path),
            "VIBE_TEST_DISABLE_KEYRING": "1",
        },
    )

    assert result.returncode == 0, result.stderr or result.stdout
