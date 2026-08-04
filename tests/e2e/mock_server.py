from __future__ import annotations

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
import time
from typing import TypedDict, cast

from tests.constants import CHAT_COMPLETIONS_PATH


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

    @staticmethod
    def _merge_tool_call_delta(
        tool_calls_by_index: dict[int, dict[str, object]],
        delta_tool_call: object,
        *,
        fallback_index: int | None,
    ) -> int | None:
        if not isinstance(delta_tool_call, dict):
            return fallback_index

        index = delta_tool_call.get("index")
        if not isinstance(index, int):
            index = fallback_index
        if index is None:
            index = len(tool_calls_by_index)

        tool_call = tool_calls_by_index.setdefault(index, {"index": index})
        for key in ("id", "type"):
            if value := delta_tool_call.get(key):
                tool_call[key] = value

        function_delta = delta_tool_call.get("function")
        if not isinstance(function_delta, dict):
            return index

        function = tool_call.setdefault("function", {})
        if not isinstance(function, dict):
            function = {}
            tool_call["function"] = function

        if name := function_delta.get("name"):
            function["name"] = name
        if arguments := function_delta.get("arguments"):
            function["arguments"] = f"{function.get('arguments', '')}{arguments}"
        return index

    @staticmethod
    def _completion_response_from_chunks(
        chunks: list[StreamChunk],
    ) -> dict[str, object]:
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, object]] = {}
        active_tool_call_index: int | None = None
        finish_reason: object = "stop"
        usage: object = {"prompt_tokens": 3, "completion_tokens": 4}
        created = 123

        for chunk in chunks:
            chunk_created = chunk.get("created")
            if isinstance(chunk_created, int):
                created = chunk_created
            if chunk_usage := chunk.get("usage"):
                usage = chunk_usage
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason") is not None:
                finish_reason = choice.get("finish_reason")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            if content := delta.get("content"):
                content_parts.append(str(content))
            if delta_tool_calls := delta.get("tool_calls"):
                if isinstance(delta_tool_calls, list):
                    for delta_tool_call in delta_tool_calls:
                        active_tool_call_index = (
                            StreamingMockServer._merge_tool_call_delta(
                                tool_calls_by_index,
                                delta_tool_call,
                                fallback_index=active_tool_call_index,
                            )
                        )

        message: dict[str, object] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        tool_calls = [
            tool_calls_by_index[index] for index in sorted(tool_calls_by_index)
        ]
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {
            "id": "mock-id",
            "object": "chat.completion",
            "created": created,
            "model": "mock-model",
            "choices": [
                {"index": 0, "message": message, "finish_reason": finish_reason}
            ],
            "usage": usage,
        }

    def __init__(
        self,
        *,
        chunk_factory: ChunkFactory | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.requests: list[ChatCompletionsRequestPayload] = []
        self._lock = threading.Lock()
        self._chunk_factory = chunk_factory
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._build_handler())
        self._scheme = "https" if ssl_context is not None else "http"
        if ssl_context is not None:
            self._server.socket = ssl_context.wrap_socket(
                self._server.socket, server_side=True
            )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self) -> None:
                if self.path != CHAT_COMPLETIONS_PATH:
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

                chunks = (
                    parent._chunk_factory(request_index, payload)
                    if parent._chunk_factory is not None
                    else parent._stream_chunks()
                )

                if not payload.get("stream"):
                    response = parent._completion_response_from_chunks(chunks)
                    response_body = json.dumps(response, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response_body)))
                    self.end_headers()
                    self.wfile.write(response_body)
                    self.wfile.flush()
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

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
        return f"{self._scheme}://127.0.0.1:{self._server.server_port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)
