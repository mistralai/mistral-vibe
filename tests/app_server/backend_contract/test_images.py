from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mistralai_vibe_local_harness.vibe import UtilityReply
from tests.mock.utils import utility_reply
from vibe.app_server import _vision
from vibe.app_server.models import FileImageSource, ImageAttachment
from vibe.app_server.protocol import SessionOptions
from vibe.app_server.session import AppServerSession
from vibe.core.config import ModelConfig
from vibe.core.types import LLMMessage

# A valid 1x1 PNG: the Harness reads the file's dimensions to cost the image.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def backend_contract_session_options(tmp_path: Path) -> SessionOptions:
    # An attached image must sit in the workspace.
    return SessionOptions(cwd=str(tmp_path))


@pytest.mark.asyncio
async def test_a_described_image_reaches_the_model_as_its_description(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """*Prepare*: A session on a model without image input, with a vision model
    on the same provider to describe images.
    *Do*: Send a file-backed image.
    *Assert*: The model gets the description in place of the image: no
    ``[Image]``, no file link, no pixels.
    """
    # Prepare
    descriptions: list[Sequence[LLMMessage]] = []

    async def describe(
        *, model: ModelConfig, messages: Sequence[LLMMessage], **_: object
    ) -> UtilityReply:
        descriptions.append(messages)
        return utility_reply("a red error dialog")

    monkeypatch.setattr(_vision, "complete_vision", describe)
    config = backend_contract_session.resources.config
    await config.update(
        {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "blind",
                    "supports_images": False,
                },
                {
                    "name": "mistral-medium-latest",
                    "provider": "mistral",
                    "alias": "sees",
                    "supports_images": True,
                },
            ],
            "active_model": "blind",
        },
        reload_runtime=True,
    )
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return backend_contract_mistral_response("seen")

    backend_contract_mistral_api.mock(side_effect=respond)
    image = tmp_path / "shot.png"
    image.write_bytes(_PNG)
    attachment = ImageAttachment(
        source=FileImageSource(path=str(image)), alias="shot.png", mime_type="image/png"
    )

    # Do
    _ = [
        event
        async for event in backend_contract_session.act(
            "what is the error?", images=[attachment]
        )
    ]

    # Assert
    assert len(descriptions) == 1
    [blind] = requests
    user = json.dumps(_user_messages(blind))
    assert '<image alias=\\"shot.png\\">' in user
    assert "a red error dialog" in user
    assert "[Image]" not in user
    assert image.resolve().as_uri() not in user
    assert "image_url" not in user


def _user_messages(payload: dict[str, Any]) -> list[Any]:
    return [message for message in payload["messages"] if message.get("role") == "user"]
