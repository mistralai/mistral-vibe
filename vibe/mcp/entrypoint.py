"""
MCP Server entry point for Vibe.

This module provides the main entry point for running Vibe in MCP server mode,
which enables remote subagent execution via the Mistral Communication Protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.server.lowlevel.server import ServerSession
from mcp.server.models import InitializationOptions, ServerCapabilities
from mcp.server.stdio import stdio_server
from rich import print as rprint

from vibe import __version__
from vibe.core.config import VibeConfig, load_dotenv_values
from vibe.core.config.harness_files import init_harness_files_manager
from vibe.core.logger import logger
from vibe.core.mcp.vibe_server import create_vibe_mcp_server


@dataclass
class Arguments:
    agent_mode: str


def parse_arguments() -> Arguments:
    parser = argparse.ArgumentParser(description="Run Mistral Vibe in MCP server mode")
    parser.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--agent-mode",
        default="subagents",
        help="Agent exposure mode: 'agents', 'subagents', or comma-separated list of specific agent names (default: subagents)",
    )
    args = parser.parse_args()
    return Arguments(agent_mode=args.agent_mode)


def setup_server_logging() -> None:
    """Configure logging for MCP server mode (stderr only)."""
    # Set up basic logging to stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )
    
    # Configure the vibe logger for stderr output
    logger.handlers.clear()  # Remove any existing handlers
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def run_mcp_server(agent_mode: str = "subagents") -> None:
    """
    Run Vibe in MCP server mode.
    
    This function:
    1. Sets up stderr logging
    2. Initializes configuration
    3. Creates and runs the MCP server with specified agent exposure mode
    4. Handles errors appropriately
    
    Args:
        agent_mode: Mode for agent exposure. Can be 'agents', 'subagents', 
                   or comma-separated list of specific agent names.
    """
    setup_server_logging()
    load_dotenv_values()
    
    # Initialize harness files manager
    init_harness_files_manager("user", "project")
    
    logger.info("Starting Vibe MCP Server")
    
    try:
        # Load Vibe configuration
        config = VibeConfig.load()
        
        # Create MCP server instance with agent mode
        server = create_vibe_mcp_server(config, agent_mode=agent_mode)
        
        # Create initialization options with VibeMCPServer capabilities
        vibe_capabilities = server.get_capabilities()
        initialization_options = InitializationOptions(
            server_name="vibe-subagent",
            server_version="1.0.0",
            instructions="Vibe subagent execution server",
            capabilities=vibe_capabilities,
        )
        
        # Run the server using stdio transport
        anyio.run(run_server_async, server, initialization_options, backend="asyncio")
        
    except KeyboardInterrupt:
        logger.info("MCP Server shutting down (KeyboardInterrupt)")
        sys.exit(0)
        
    except EOFError:
        logger.info("MCP Server shutting down (EOF)")
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"MCP Server failed: {e}")
        rprint(f"[red]MCP Server error: {e}[/]", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main() -> None:
    """Main entry point for vibe-mcp command."""
    args = parse_arguments()
    run_mcp_server(agent_mode=args.agent_mode)


async def run_server_async(
    server: Any, 
    initialization_options: InitializationOptions
) -> None:
    """Async function to run the MCP server."""
    logger.info("Starting server with stdio transport...")
    
    async with stdio_server() as (read_stream, write_stream):
        logger.info("Got stdio streams, starting server...")
        
        # Use the server.run method which handles session creation and message processing
        try:
            await server.run(
                read_stream,
                write_stream,
                initialization_options,
            )
            logger.info("Server.run completed")
        except Exception as e:
            logger.error(f"Server.run failed: {e}")
            raise


if __name__ == "__main__":
    main()
