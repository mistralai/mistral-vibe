from __future__ import annotations

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import TypedDict, cast


class StreamOptionsPayload(TypedDict, total=False):
    include_usage: bool
    stream_tool_calls: bool


class ChatMessagePayload(TypedDict, total=False):
    role: str
    content: str


class ChatCompletionsRequestPayload(TypedDict, total=False):
    model: str
    messages: list[ChatMessagePayload]
    stream: bool
    stream_options: StreamOptionsPayload


type StreamChunk = dict[str, object]
type ChunkFactory = Callable[[int, ChatCompletionsRequestPayload], list[StreamChunk]]


class StreamingMockServer:
    @staticmethod
    def build_chunk(
        *,
        created: int,
        delta: dict[str, object],
        finish_reason: str | None,
        usage: dict[str, int] | None = None,
    ) -> StreamChunk:
        chunk: dict[str, object] = {
            "id": "mock-id",
            "object": "chat.completion.chunk",
            "created": created,
            "model": "mock-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            chunk["usage"] = usage
        return chunk

    @staticmethod
    def build_tool_call_delta(
        *, call_id: str, tool_name: str, arguments: str, index: int = 0
    ) -> dict[str, object]:
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": arguments},
                }
            ],
        }

    @staticmethod
    def _stream_chunks() -> list[StreamChunk]:
        return [
            StreamingMockServer.build_chunk(
                created=123,
                delta={"role": "assistant", "content": "Hello"},
                finish_reason=None,
            ),
            StreamingMockServer.build_chunk(
                created=124, delta={"content": " from mock server"}, finish_reason=None
            ),
            StreamingMockServer.build_chunk(
                created=125,
                delta={},
                finish_reason="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 4},
            ),
        ]

    def __init__(self, *, chunk_factory: ChunkFactory | None = None) -> None:
        self.requests: list[ChatCompletionsRequestPayload] = []
        self._lock = threading.Lock()
        self._chunk_factory = chunk_factory
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self.send_response(404)
                    self.end_headers()
                    return

                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                payload = cast(
                    ChatCompletionsRequestPayload, json.loads(body.decode("utf-8"))
                )

                with parent._lock:
                    parent.requests.append(payload)
                    request_index = len(parent.requests) - 1

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                chunks = (
                    parent._chunk_factory(request_index, payload)
                    if parent._chunk_factory is not None
                    else parent._stream_chunks()
                )

                for chunk in chunks:
                    data = json.dumps(chunk, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.03)

                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        return Handler

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)
