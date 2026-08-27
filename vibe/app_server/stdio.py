from __future__ import annotations

import argparse
import asyncio

from vibe._experimental_harness import add_experimental_harness_argument
from vibe.app_server._runtime import create_harness_server
from vibe.app_server.transport import (
    BinaryLineReader,
    BinaryLineWriter,
    StdioJsonRpcTransport,
)
from vibe.core.config.harness_files import init_harness_files_manager
from vibe.core.paths import LOG_FILE
from vibe.observability.logging import init_file_logging


async def serve_stdio(
    *,
    reader: BinaryLineReader | None = None,
    writer: BinaryLineWriter | None = None,
    experimental_harness: bool = False,
) -> None:
    transport = (
        StdioJsonRpcTransport.from_standard_streams()
        if reader is None or writer is None
        else StdioJsonRpcTransport(reader, writer)
    )
    harness = await create_harness_server(
        transport, transport_kind="stdio", experimental_harness=experimental_harness
    )
    await harness.serve()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mistral Vibe app server")
    add_experimental_harness_argument(parser)
    return parser.parse_args()


def main() -> None:
    from vibe.core.config import load_dotenv_values
    from vibe.core.utils.windows_asyncio import (
        silence_proactor_transport_teardown_warnings,
    )

    silence_proactor_transport_teardown_warnings()
    args = parse_arguments()
    init_harness_files_manager("user", "project")
    init_file_logging(LOG_FILE.path)
    load_dotenv_values()
    asyncio.run(serve_stdio(experimental_harness=args.experimental_harness))
