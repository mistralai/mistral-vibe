"""Tests for vibe.core.paths._vibe_home

Covers the three-context policy for resolving VIBE_HOME:
1. CLI mode       -> ~/.vibe
2. ACP mode       -> {cwd}/.vibe
3. Explicit override -> $VIBE_HOME
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from vibe.core.paths._vibe_home import (
    VIBE_HOME,
    is_acp_mode,
    resolve_vibe_home,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_dir() -> Path:
    """Return a temporary directory that is cleaned up automatically."""
    return Path(tempfile.mkdtemp())


# ---------------------------------------------------------------------------
# is_acp_mode
# ---------------------------------------------------------------------------

class TestIsAcpMode:
    """Tests for the is_acp_mode() sentinel."""

    def test_returns_true_when_env_var_set(self):
        with patch.dict(os.environ, {"ACP_MODE": "1"}):
            assert is_acp_mode() is True

    def test_returns_false_when_env_var_absent(self):
        # Make sure ACP_MODE is not set
        env = {k: v for k, v in os.environ.items() if k != "ACP_MODE"}
        with patch.dict(os.environ, env, clear=True):
            assert is_acp_mode() is False

    def test_returns_false_when_env_var_set_to_other_value(self):
        with patch.dict(os.environ, {"ACP_MODE": "0"}):
            assert is_acp_mode() is False

    def test_returns_false_when_env_var_empty(self):
        with patch.dict(os.environ, {"ACP_MODE": ""}):
            assert is_acp_mode() is False


# ---------------------------------------------------------------------------
# resolve_vibe_home — policy tests
# ---------------------------------------------------------------------------

class TestResolveVibeHome:
    """Tests for resolve_vibe_home(cwd) policy decisions."""

    # --- Case 3: explicit VIBE_HOME env var always wins ------------------

    def test_explicit_vibe_home_overrides_acp_mode(self):
        """VIBE_HOME wins even when ACP_MODE is set."""
        cwd = _make_temp_dir()
        with patch.dict(os.environ, {"VIBE_HOME": "/custom/vibe", "ACP_MODE": "1"}):
            result = resolve_vibe_home(cwd)
            assert result == Path("/custom/vibe").expanduser().resolve()

    def test_explicit_vibe_home_expands_tilde(self):
        cwd = _make_temp_dir()
        with patch.dict(os.environ, {"VIBE_HOME": "~/my-vibe"}):
            result = resolve_vibe_home(cwd)
            assert result == (Path.home() / "my-vibe").resolve()

    def test_explicit_vibe_home_absolute_path(self):
        cwd = _make_temp_dir()
        custom = _make_temp_dir() / "my-vibe"
        with patch.dict(os.environ, {"VIBE_HOME": str(custom)}):
            result = resolve_vibe_home(cwd)
            assert result == custom.resolve()

    # --- Case 2: ACP mode -> project-local .vibe ------------------------

    def test_acp_mode_returns_cwd_dot_vibe(self):
        """In ACP mode without VIBE_HOME set, use {cwd}/.vibe."""
        cwd = _make_temp_dir()
        expected = (cwd / ".vibe").resolve()
        with patch.dict(os.environ, {"ACP_MODE": "1"}, clear=True):
            # Make sure VIBE_HOME is not set
            env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
            with patch.dict(os.environ, env, clear=True):
                os.environ["ACP_MODE"] = "1"
                result = resolve_vibe_home(cwd)
                assert result == expected

    def test_acp_mode_creates_no_side_effects_in_cwd(self):
        """resolve_vibe_home must NOT create directories — that is bootstrap's job."""
        cwd = _make_temp_dir()
        with patch.dict(os.environ, {"ACP_MODE": "1"}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
            with patch.dict(os.environ, env, clear=True):
                os.environ["ACP_MODE"] = "1"
                resolve_vibe_home(cwd)
                assert not (cwd / ".vibe").exists()

    # --- Case 1: CLI mode -> ~/.vibe --------------------------------

    def test_cli_mode_returns_default_home(self):
        """Without ACP_MODE or VIBE_HOME, use ~/.vibe."""
        cwd = _make_temp_dir()
        expected = (Path.home() / ".vibe").resolve()
        env = {k: v for k, v in os.environ.items() if k not in ("VIBE_HOME", "ACP_MODE")}
        with patch.dict(os.environ, env, clear=True):
            result = resolve_vibe_home(cwd)
            assert result == expected

    # --- Path resolution behaviour ----------------------------------------

    def test_cwd_is_resolved_to_absolute(self):
        """The returned path should be absolute even if cwd is relative."""
        cwd = _make_temp_dir()
        with patch.dict(os.environ, {"ACP_MODE": "1"}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
            with patch.dict(os.environ, env, clear=True):
                os.environ["ACP_MODE"] = "1"
                result = resolve_vibe_home(cwd)
                assert result.is_absolute()

    def test_cwd_symlink_is_resolved(self):
        """resolve_vibe_home should return a resolved (symlink-free) path."""
        cwd = _make_temp_dir()
        # Create a symlink to cwd
        link_dir = _make_temp_dir().parent / "link_to_cwd"
        try:
            os.symlink(cwd, link_dir)
            with patch.dict(os.environ, {"ACP_MODE": "1"}, clear=True):
                env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
                with patch.dict(os.environ, env, clear=True):
                    os.environ["ACP_MODE"] = "1"
                    result = resolve_vibe_home(link_dir)
                    # Result should be resolved (not contain the symlink)
                    assert result == (cwd / ".vibe").resolve()
        finally:
            if link_dir.exists():
                os.unlink(link_dir)


# ---------------------------------------------------------------------------
# VIBE_HOME GlobalPath integration
# ---------------------------------------------------------------------------

class TestVibeHomeGlobalPath:
    """Tests for the VIBE_HOME GlobalPath integration."""

    def test_default_vibe_home_is_home_dot_vibe(self):
        """Without overrides, VIBE_HOME points to ~/.vibe."""
        env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
        with patch.dict(os.environ, env, clear=True):
            assert VIBE_HOME.path == (Path.home() / ".vibe").resolve()

    def test_vibe_home_respects_env_override(self):
        """VIBE_HOME GlobalPath respects VIBE_HOME env var."""
        custom = _make_temp_dir() / "custom-vibe"
        with patch.dict(os.environ, {"VIBE_HOME": str(custom)}):
            assert VIBE_HOME.path == custom.resolve()

    def test_vibe_home_expands_tilde(self):
        with patch.dict(os.environ, {"VIBE_HOME": "~/.my-vibe"}):
            assert VIBE_HOME.path == (Path.home() / ".my-vibe").resolve()

    def test_plans_dir_is_under_vibe_home(self):
        """PLANS_DIR should always be a subdir of VIBE_HOME."""
        from vibe.core.paths import PLANS_DIR

        env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
        with patch.dict(os.environ, env, clear=True):
            assert PLANS_DIR.path == (Path.home() / ".vibe" / "plans").resolve()


# ---------------------------------------------------------------------------
# Bootstrap / directory creation
# ---------------------------------------------------------------------------

class TestBootstrap:
    """Tests for directory bootstrapping in ACP mode."""

    def test_acp_mode_bootstrap_creates_vibe_home(self):
        """_resolve_and_bootstrap_vibe_home creates the .vibe dir."""
        from vibe.acp.acp_agent_loop import VibeAcpAgentLoop

        cwd = _make_temp_dir()
        agent = VibeAcpAgentLoop()

        # Patch is_acp_mode to return True
        with patch("vibe.acp.acp_agent_loop.is_acp_mode", return_value=True):
            with patch.dict(os.environ, {}, clear=True):
                result = agent._resolve_and_bootstrap_vibe_home(str(cwd))

                # Directory should be created
                assert result.exists()
                assert result.is_dir()
                assert result == (cwd / ".vibe").resolve()

    def test_acp_mode_bootstrap_creates_plans_dir(self):
        """Plans directory should be created during bootstrap."""
        from vibe.acp.acp_agent_loop import VibeAcpAgentLoop

        cwd = _make_temp_dir()
        agent = VibeAcpAgentLoop()

        with patch("vibe.acp.acp_agent_loop.is_acp_mode", return_value=True):
            with patch.dict(os.environ, {}, clear=True):
                result = agent._resolve_and_bootstrap_vibe_home(str(cwd))
                plans_dir = result / "plans"
                assert plans_dir.exists()
                assert plans_dir.is_dir()

    def test_acp_mode_bootstrap_creates_logs_session_dir(self):
        """Session log directory should be created during bootstrap."""
        from vibe.acp.acp_agent_loop import VibeAcpAgentLoop

        cwd = _make_temp_dir()
        agent = VibeAcpAgentLoop()

        with patch("vibe.acp.acp_agent_loop.is_acp_mode", return_value=True):
            with patch.dict(os.environ, {}, clear=True):
                result = agent._resolve_and_bootstrap_vibe_home(str(cwd))
                session_dir = result / "logs" / "session"
                assert session_dir.exists()
                assert session_dir.is_dir()

    def test_bootstrap_idempotent(self):
        """Running bootstrap twice should not fail."""
        from vibe.acp.acp_agent_loop import VibeAcpAgentLoop

        cwd = _make_temp_dir()
        agent = VibeAcpAgentLoop()

        with patch("vibe.acp.acp_agent_loop.is_acp_mode", return_value=True):
            with patch.dict(os.environ, {}, clear=True):
                agent._resolve_and_bootstrap_vibe_home(str(cwd))
                # Second call should not raise
                agent._resolve_and_bootstrap_vibe_home(str(cwd))


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge case tests for path resolution."""

    def test_vibe_home_with_trailing_slash(self):
        """VIBE_HOME with trailing slash should still work."""
        cwd = _make_temp_dir()
        custom = _make_temp_dir() / "my-vibe"
        with patch.dict(os.environ, {"VIBE_HOME": str(custom) + "/"}):
            result = resolve_vibe_home(cwd)
            assert result == custom.resolve()

    def test_vibe_home_env_var_empty_string(self):
        """Empty VIBE_HOME should fall through to next policy."""
        cwd = _make_temp_dir()
        env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
        with patch.dict(os.environ, env, clear=True):
            # ACP_MODE is not set, so should fall through to default
            result = resolve_vibe_home(cwd)
            assert result == (Path.home() / ".vibe").resolve()

    def test_acp_mode_with_explicit_vibe_home_empty(self):
        """ACP mode + empty VIBE_HOME should use cwd/.vibe."""
        cwd = _make_temp_dir()
        with patch.dict(os.environ, {"ACP_MODE": "1", "VIBE_HOME": ""}):
            result = resolve_vibe_home(cwd)
            assert result == (cwd / ".vibe").resolve()

    def test_cwd_with_spaces(self):
        """cwd with spaces should work correctly."""
        cwd = Path(tempfile.mkdtemp(prefix="test dir with spaces"))
        with patch.dict(os.environ, {"ACP_MODE": "1"}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "VIBE_HOME"}
            with patch.dict(os.environ, env, clear=True):
                os.environ["ACP_MODE"] = "1"
                result = resolve_vibe_home(cwd)
                assert result == (cwd / ".vibe").resolve()
                # Cleanup
                import shutil
                shutil.rmtree(cwd, ignore_errors=True)
