from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from vibe.core.utils.sse import iter_sse_lines


class _ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


async def _collect(chunks: list[bytes]) -> list[str]:
    response = httpx.Response(
        status_code=200,
        stream=_ChunkedStream(chunks),
        request=httpx.Request("POST", "https://example.com"),
    )
    return [line async for line in iter_sse_lines(response)]


class TestIterSseLines:
    @pytest.mark.asyncio
    async def test_splits_on_lf_and_crlf(self) -> None:
        lines = await _collect([b"data: a\r\ndata: b\n\ndata: c\n"])
        assert lines == ["data: a", "data: b", "", "data: c"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\x0b", "\x0c"])
    async def test_keeps_unicode_line_breaks_within_line(self, separator: str) -> None:
        payload = f'data: {{"arguments": "{separator}value"}}\n'
        lines = await _collect([payload.encode()])
        assert lines == [payload.removesuffix("\n")]

    @pytest.mark.asyncio
    async def test_buffers_partial_lines_across_chunks(self) -> None:
        lines = await _collect([b"data: one", b"two\ndata: three", b"four\n"])
        assert lines == ["data: onetwo", "data: threefour"]

    @pytest.mark.asyncio
    async def test_multibyte_char_split_across_chunks(self) -> None:
        encoded = "data: café\n".encode()
        lines = await _collect([encoded[:9], encoded[9:]])
        assert lines == ["data: café"]

    @pytest.mark.asyncio
    async def test_yields_trailing_line_without_newline(self) -> None:
        lines = await _collect([b"data: a\ndata: b"])
        assert lines == ["data: a", "data: b"]

    @pytest.mark.asyncio
    async def test_crlf_split_across_chunks(self) -> None:
        lines = await _collect([b"data: a\r", b"\ndata: b\n"])
        assert lines == ["data: a", "data: b"]

    @pytest.mark.asyncio
    async def test_splits_on_lone_cr(self) -> None:
        lines = await _collect([b"data: a\rdata: b\r"])
        assert lines == ["data: a", "data: b"]

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        assert await _collect([]) == []

    @pytest.mark.asyncio
    async def test_replaces_undecodable_bytes(self) -> None:
        lines = await _collect([b"data: a\xff b\n"])
        assert lines == ["data: a� b"]
