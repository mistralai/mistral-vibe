"""
Integration test for MCP Server.

This module tests the MCP server integration with the CLI.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest


class TestMCPServerIntegration:
    """Test MCP Server integration."""

    def test_mcp_server_flag_exists(self):
        """Test that --mcp-server flag is available in CLI."""
        result = subprocess.run(
            [sys.executable, "-m", "vibe.cli.entrypoint", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        
        assert result.returncode == 0
        assert "--mcp-server" in result.stdout
        assert "Run in MCP server mode instead of interactive UI" in result.stdout

    def test_mcp_server_starts(self):
        """Test that MCP server starts without crashing."""
        # Start the server process with timeout to ensure it doesn't crash immediately
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "vibe.cli.entrypoint", "--mcp-server"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=Path(__file__).parent.parent.parent,
            )
            
            # Give the server a moment to start
            time.sleep(0.5)
            
            # Check if the process is still running (should be waiting for messages)
            if proc.poll() is None:
                # Process is still running, which is expected
                proc.terminate()
                proc.wait(timeout=2)
                assert True  # Server started successfully
            else:
                # Process exited, check why
                stdout, stderr = proc.communicate()
                print(f"Server stdout: {stdout}")
                print(f"Server stderr: {stderr}")
                assert False, f"Server exited unexpectedly with code {proc.returncode}"
                
        except Exception as e:
            print(f"Test failed with exception: {e}")
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
