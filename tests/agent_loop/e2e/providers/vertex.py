from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
import respx

from tests import constants as c
from tests.agent_loop.e2e.providers.anthropic import Mocks
from tests.agent_loop.e2e.providers.base import ProviderAPI
from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.types import Backend

__all__ = ["API", "Mocks", "e2e_config"]


def e2e_config(**overrides: Any) -> VibeConfig:
    provider = ProviderConfig(
        name="vertex",
        api_base="",
        api_key_env_var="VERTEX_API_KEY",
        api_style="vertex-anthropic",
        backend=Backend.GENERIC,
        project_id=c.VERTEX_PROJECT_ID,
        region=c.VERTEX_REGION,
    )
    models = [ModelConfig(name=c.VERTEX_MODEL, provider="vertex", alias="vertex")]
    return build_test_vibe_config(
        active_model="vertex", models=models, providers=[provider], **overrides
    )


class API(ProviderAPI):
    """Vertex splits rawPredict and streamRawPredict across two wire paths."""

    base_url = c.VERTEX_BASE_URL
    post_path = c.VERTEX_RAW_PREDICT_PATH

    def setup_router(self, router: respx.MockRouter) -> None:
        super().setup_router(router)
        self._stream_route = router.post(c.VERTEX_STREAM_PREDICT_PATH)

    @classmethod
    def setup_monkeypatch(cls, monkeypatch: pytest.MonkeyPatch) -> None:
        # google.auth is the only external dependency Vertex pulls in; stub the
        # credential lookup so the real token-refresh logic runs without network.
        creds = MagicMock()
        creds.valid = True
        creds.token = "fake-vertex-token"
        monkeypatch.setattr(
            "vibe.core.llm.backend.vertex.google.auth.default",
            lambda **_kwargs: (creds, c.VERTEX_PROJECT_ID),
        )

    def reply_stream(self, chunks: list[bytes]) -> None:
        self._stream_route.mock(return_value=self._stream_response(chunks))

    def reply_streams(self, *chunk_lists: list[bytes]) -> None:
        self._stream_route.mock(
            side_effect=[self._stream_response(chunks) for chunks in chunk_lists]
        )

    @property
    def request_json(self) -> dict[str, Any]:
        route = self._stream_route if self._stream_route.calls else self.route
        return json.loads(route.calls.last.request.content)
