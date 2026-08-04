from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx


async def iter_sse_lines(response: httpx.Response) -> AsyncGenerator[str]:
    # SSE delimits lines with CRLF, LF or CR only, but httpx's aiter_lines()
    # follows str.splitlines() and also breaks on U+2028/U+0085/..., which are
    # valid unescaped inside JSON strings, truncating payloads mid-line.
    buffer = b""
    async for chunk in response.aiter_bytes():
        buffer += chunk
        # A trailing CR may be the first half of a CRLF split across chunks.
        held_cr = buffer.endswith(b"\r")
        if held_cr:
            buffer = buffer[:-1]
        *lines, buffer = (
            buffer.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        )
        if held_cr:
            buffer += b"\r"
        for line in lines:
            yield line.decode("utf-8", errors="replace")
    if buffer:
        yield buffer.removesuffix(b"\r").decode("utf-8", errors="replace")
