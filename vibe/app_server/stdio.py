from __future__ import annotations

from vibe.app_server._runtime import create_harness_server
from vibe.app_server.transport import (
    BinaryLineReader,
    BinaryLineWriter,
    StdioJsonRpcTransport,
)


async def serve_stdio(
    *, reader: BinaryLineReader | None = None, writer: BinaryLineWriter | None = None
) -> None:
    transport = (
        StdioJsonRpcTransport.from_standard_streams()
        if reader is None or writer is None
        else StdioJsonRpcTransport(reader, writer)
    )
    harness = await create_harness_server(transport, transport_kind="stdio")
    await harness.serve()
