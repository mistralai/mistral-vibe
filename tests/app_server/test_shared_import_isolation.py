from __future__ import annotations

import subprocess
import sys
import textwrap


def test_shared_server_helpers_do_not_import_legacy_execution() -> None:
    """Unified and historical-session reads must survive deletion of the loop."""
    script = textwrap.dedent(
        """
        import importlib
        import sys
        from importlib.abc import MetaPathFinder

        forbidden = (
            "vibe.core.agent_loop",
            "vibe.core.agent_loop_hooks",
            "vibe.core.session.session_logger",
            "vibe.core.session.last_session_pointer",
            "vibe.app_server._legacy_composition",
            "vibe.app_server._legacy_session_backend",
            "vibe.app_server._legacy_session_runtime",
            "vibe.app_server._legacy_projection",
            "vibe.app_server._legacy_state",
            "vibe.app_server._legacy_workspace",
            "vibe.app_server._legacy_root_session",
            "vibe.app_server._legacy_vibe_code",
        )

        class Blocker(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(
                    fullname == name or fullname.startswith(name + ".")
                    for name in forbidden
                ):
                    raise ImportError(f"legacy execution dependency: {fullname}")
                return None

        sys.meta_path.insert(0, Blocker())
        for module in (
            "vibe.app_server._runtime",
            "vibe.app_server.local",
            "vibe.app_server.stdio",
            "vibe.app_server.server",
            "vibe.app_server._projection",
            "vibe.app_server._state",
            "vibe.app_server._workspace",
            "vibe.app_server._root_session",
            "vibe.app_server._vibe_code",
            "vibe.app_server._utils",
            "vibe.app_server._legacy_import",
            "vibe.app_server._unified_harness_backend_adapter",
        ):
            importlib.import_module(module)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
