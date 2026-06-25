from __future__ import annotations

import json
from typing import Any, ClassVar, Protocol

import httpx
import pytest
import respx

from tests.backend.data import Chunk, JsonResponse
from vibe.core.types import AssistantEvent, BaseEvent


def assistant_text(events: list[BaseEvent]) -> str:
    return "".join(
        e.content for e in events if isinstance(e, AssistantEvent) and e.content
    ).strip()


class ProviderMocks(Protocol):
    """Canonical wire payloads a provider must produce for the shared e2e suite."""

    def answer(self, text: str) -> JsonResponse:
        """A non-streaming assistant turn that replies with `text`."""
        ...

    def text_stream(self, text: str) -> list[Chunk]:
        """A streaming assistant turn whose deltas reassemble into `text`."""
        ...

    def tool_call(self, name: str, arguments: dict[str, Any]) -> JsonResponse:
        """A non-streaming turn that calls tool `name` with `arguments`."""
        ...

    def reasoning_answer(self, text: str, reasoning: str) -> JsonResponse:
        """A non-streaming turn that thinks `reasoning`, then replies `text`."""
        ...

    def reasoning_tool_call_stream(
        self, name: str, arguments: dict[str, Any], *, reasoning: str
    ) -> list[Chunk]:
        """A streamed turn that thinks `reasoning`, then calls tool `name`."""
        ...


class ProviderAPI:
    """Stubs a provider's completion wire; the AgentLoop stays the subject.

    Subclasses describe one provider via `base_url`/`post_path`; override
    `setup_monkeypatch` for non-HTTP stubs (e.g. credentials) a provider needs.
    """

    base_url: ClassVar[str]
    post_path: ClassVar[str]

    route: respx.Route

    def setup_router(self, router: respx.MockRouter) -> None:
        """Bind the completion route; subclasses extend for extra wires/defaults."""
        self.route = router.post(self.post_path)

    @classmethod
    def setup_monkeypatch(cls, monkeypatch: pytest.MonkeyPatch) -> None:
        """Apply non-HTTP stubs (e.g. credential lookups) before use."""

    def reply(self, *completions: dict[str, Any]) -> None:
        responses = [httpx.Response(200, json=completion) for completion in completions]
        if len(responses) == 1:
            self.route.mock(return_value=responses[0])
        else:
            self.route.mock(side_effect=responses)

    def reply_stream(self, chunks: list[bytes]) -> None:
        self.route.mock(return_value=self._stream_response(chunks))

    def reply_streams(self, *chunk_lists: list[bytes]) -> None:
        self.route.mock(
            side_effect=[self._stream_response(chunks) for chunks in chunk_lists]
        )

    @staticmethod
    def _stream_response(chunks: list[bytes]) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
            headers={"Content-Type": "text/event-stream"},
        )

    @property
    def request_json(self) -> dict[str, Any]:
        return json.loads(self.route.calls.last.request.content)

    def model_facing_text(self, index: int) -> str:
        """Return the text (raw prompt) that the model would have seen in the request
        at the given call index.
        """
        body = json.loads(self.route.calls[index].request.content)
        return json.dumps([m for m in body["messages"] if m.get("role") != "system"])
