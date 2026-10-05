from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import sys

from vibe._experimental_harness import (
    ExperimentalHarnessUnavailableError,
    add_experimental_harness_argument,
)
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


async def serve_stdio(
    *,
    reader: BinaryLineReader | None = None,
    writer: BinaryLineWriter | None = None,
    experimental_harness: bool = False,
    legacy_harness: bool = False,
    multiplex: bool = False,
    additional_builtin_plugin_roots: Sequence[Path] = (),
) -> None:
    transport = (
        StdioJsonRpcTransport.from_standard_streams()
        if reader is None or writer is None
        else StdioJsonRpcTransport(reader, writer)
    )
    if multiplex:
        if not experimental_harness or legacy_harness:
            raise ValueError("Multiplexed stdio requires the Unified Harness")

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
                    experimental_harness=True,
                    additional_builtin_plugin_roots=additional_builtin_plugin_roots,
                ),
            )

        await serve_multiplexed(transport, create_channel)
        return
    harness = await create_harness_server(
        transport,
        transport_kind="stdio",
        experimental_harness=experimental_harness,
        legacy_harness=legacy_harness,
        additional_builtin_plugin_roots=additional_builtin_plugin_roots,
    )
    await harness.serve()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mistral Vibe app server")
    parser.add_argument(
        "--multiplex",
        action="store_true",
        help="Serve isolated Unified Harness connections in one stdio process.",
    )
    harness_group = parser.add_mutually_exclusive_group()
    add_experimental_harness_argument(parser, group=harness_group)
    harness_group.add_argument(
        "--legacy-harness",
        action="store_true",
        default=False,
        help=(
            "Force the legacy Python harness. Temporary escape hatch, kept "
            "until the legacy runtime is removed."
        ),
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


def main() -> None:
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
    init_harness_files_manager("user", "project")
    init_file_logging(LOG_FILE.path)
    load_dotenv_values()
    # The Unified Harness is the required default runtime: a missing,
    # incompatible, or failed Runtime must abort startup with an actionable
    # error instead of serving on the legacy fallback. Stdout stays clean for
    # the JSON-RPC stream; the error goes to stderr with a non-zero exit.
    try:
        asyncio.run(
            serve_stdio(
                experimental_harness=args.experimental_harness,
                legacy_harness=args.legacy_harness,
                multiplex=args.multiplex,
                additional_builtin_plugin_roots=args.additional_builtin_plugin_roots,
            )
        )
    except ExperimentalHarnessUnavailableError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _neutralize_stdout()


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
    except (OSError, ValueError):
        pass
    finally:
        os.close(devnull)
