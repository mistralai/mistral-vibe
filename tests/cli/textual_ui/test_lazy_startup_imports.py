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


def test_importing_app_server_local_does_not_import_mcp_package() -> None:
    code = """
import sys
import vibe.app_server.local

blocked = [
    "vibe.core.tools.mcp.tools",
    "mcp",
    "mistralai",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected app server modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_importing_cli_entrypoint_does_not_import_git() -> None:
    code = """
import sys
import vibe.cli.entrypoint

if "git" in sys.modules:
    raise SystemExit("unexpected git module loaded")
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
    "mistralai",
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
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)


class _Orchestrator:
    def __init__(self, config):
        self._base_config = config
        self._config = config
        self._layers = []

    @property
    def config(self):
        return self._config

    @property
    def layers(self):
        return tuple(self._layers)

    def insert_layer(self, layer, index):
        self._layers.insert(index, layer)

    def remove_layer(self, index):
        return self._layers.pop(index)

    def replace_or_append_layer(self, name, layer):
        index = next(
            (i for i, existing in enumerate(self._layers) if existing.name == name),
            None,
        )
        if index is None:
            self._layers.append(layer)
            return
        self._layers.pop(index)
        self._layers.insert(index, layer)

    def rebuild(self):
        from vibe.core.config.builder import ConfigBuilder
        from vibe.core.config.layers.overrides import OverridesLayer
        from vibe.core.utils.concurrency import run_sync

        builder = ConfigBuilder(type(self._base_config))
        builder.add_layer(
            OverridesLayer(
                data=self._base_config.model_dump(mode="json"), name="fake-base"
            )
        )
        builder.add_layers(list(self._layers))
        self._config = run_sync(builder.build())

    async def set_field(self, *args, **kwargs):
        return []

    async def reload(self):
        return None


class Backend:
    async def complete(self, **kwargs):
        raise AssertionError

    async def __aexit__(self, *args):
        return None


init_harness_files_manager("user", "project")
try:
    config = VibeConfigSchema(
        enable_connectors=False,
        session_logging=SessionLoggingConfig(enabled=False),
    )
    loop = AgentLoop(
        _Orchestrator(config),
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
