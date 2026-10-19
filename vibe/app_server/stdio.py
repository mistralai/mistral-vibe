from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from vibe._experimental_harness import ExperimentalHarnessUnavailableError
from vibe.app_server._runtime import (
    HarnessProcess,
    HarnessServer,
    create_harness_server,
)
from vibe.app_server.multiplex import serve_multiplexed
from vibe.app_server.transport import (
    BinaryLineReader,
    BinaryLineWriter,
    JsonRpcTransport,
    StdioJsonRpcTransport,
)
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    init_harness_files_manager,
)
from vibe.core.paths import LOG_FILE, bootstrap_vibe_home
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.observability.logging import init_file_logging

if TYPE_CHECKING:
    from vibe.app_server._agent_socket import AgentSocketMount


async def serve_stdio(
    *,
    reader: BinaryLineReader | None = None,
    writer: BinaryLineWriter | None = None,
    multiplex: bool = False,
    additional_builtin_plugin_roots: Sequence[Path] = (),
    agent_socket: AgentSocketMount | None = None,
) -> None:
    transport = (
        StdioJsonRpcTransport.from_standard_streams()
        if reader is None or writer is None
        else StdioJsonRpcTransport(reader, writer)
    )
    if multiplex:

        async def create_channel(channel: JsonRpcTransport) -> HarnessServer:
            harness_files = HarnessFilesManager(
                sources=("user", "project"),
                trust_store=trusted_folders_manager.for_session(),
            )
            return await create_harness_server(
                channel,
                transport_kind="stdio",
                process=HarnessProcess(
                    harness_files,
                    additional_builtin_plugin_roots=additional_builtin_plugin_roots,
                ),
            )

        await serve_multiplexed(transport, create_channel)
        return
    if agent_socket is None:
        harness = await create_harness_server(
            transport,
            transport_kind="stdio",
            additional_builtin_plugin_roots=additional_builtin_plugin_roots,
        )
    else:
        harness = await create_harness_server(
            transport,
            transport_kind="stdio",
            process=HarnessProcess(
                additional_builtin_plugin_roots=additional_builtin_plugin_roots,
                agent_socket=agent_socket,
            ),
        )
    await harness.serve()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mistral Vibe app server")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--multiplex",
        action="store_true",
        help="Serve isolated Unified Harness connections in one stdio process.",
    )
    # Experimental and hidden, like `vibe -p --agent-socket`, which passes it:
    # the protocol (vibe.app_server._agent_socket) may change without notice.
    mode.add_argument(
        "--agent-socket", type=Path, metavar="PATH", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--additional-builtin-plugin-root",
        action="append",
        dest="additional_builtin_plugin_roots",
        default=[],
        type=Path,
        metavar="PATH",
        help=(
            "Directory to discover built-in plugins from, in addition to the "
            "ones shipped inside the package. May be given more than once."
        ),
    )
    return parser.parse_args()


_PROCESS_TITLE = "vibe-app-server"


def _set_process_title() -> None:
    # Cosmetic, so it must never block startup: setproctitle can fail on exotic
    # platforms or frozen builds. No-op for Windows Task Manager.
    try:
        import setproctitle

        setproctitle.setproctitle(_PROCESS_TITLE)
    except Exception:
        pass


def main() -> None:
    _set_process_title()
    from vibe.core.config import load_dotenv_values
    from vibe.core.utils.windows_asyncio import (
        silence_proactor_transport_teardown_warnings,
    )

    silence_proactor_transport_teardown_warnings()
    # The gate must run before the harness files manager and file logging:
    # their mkdir(parents=True) calls are otherwise the first to materialize
    # ~/.vibe, at permissive modes.
    bootstrap_vibe_home()
    args = parse_arguments()
    init_file_logging(LOG_FILE.path)
    load_dotenv_values()
    agent_socket = (
        None if args.agent_socket is None else _connect_agent_socket(args.agent_socket)
    )
    if agent_socket is None or agent_socket.sandbox is None:
        init_harness_files_manager("user", "project")
    else:
        # The project lives in the sandbox: its files on this host's disk at
        # the same path are not the project's.
        init_harness_files_manager("user")
    # Finish reaps left by earlier processes, in the background.
    from vibe.app_server import _worktree_cleanup

    _worktree_cleanup.worktree_cleanup().sweep()
    # The Unified Harness is the required default runtime: a missing,
    # incompatible, or failed Runtime must abort startup with an actionable
    # error instead of serving on the legacy fallback. Stdout stays clean for
    # the JSON-RPC stream; the error goes to stderr with a non-zero exit.
    try:
        asyncio.run(
            serve_stdio(
                multiplex=args.multiplex,
                additional_builtin_plugin_roots=args.additional_builtin_plugin_roots,
                agent_socket=agent_socket,
            )
        )
    except ExperimentalHarnessUnavailableError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _neutralize_stdout()


def _connect_agent_socket(path: Path) -> AgentSocketMount:
    """Handshake before serving: whether the server serves a sandbox decides
    which harness files load. A failed handshake fails every session open.
    """
    from vibe.app_server import _agent_socket

    return asyncio.run(_agent_socket.AgentSocketMount.connect(path.expanduser()))


def _neutralize_stdout() -> None:
    """Prevent a disconnected client from breaking the interpreter's final flush."""
    import os
    import sys

    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(devnull, sys.stdout.fileno())
    except OSError, ValueError:
        pass
    finally:
        os.close(devnull)
