"""Tests for the vision capability guard in AgentLoop._conversation_loop."""

from __future__ import annotations

from pathlib import Path
import struct
import zlib

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config import Backend, ModelConfig, ProviderConfig
from vibe.core.types import ImageContentPart, Role

# ── helpers ───────────────────────────────────────────────────────────────────


def _sent_user_msg(backend: FakeBackend, call_idx: int = 0):
    """Return the last user-role message from a backend call.

    FakeBackend stores a reference to the live messages list, so by the time
    the test reads it the assistant reply has already been appended.  We
    therefore look for the last message with role=user explicitly.
    """
    msgs = backend.requests_messages[call_idx]
    user_msgs = [m for m in msgs if m.role == Role.user]
    return user_msgs[-1]


def make_png(path: Path) -> None:
    def chunk(name: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def _vision_config(supports_vision: bool):
    models = [
        ModelConfig(
            name="test-model",
            provider="mistral",
            alias="test-model",
            supports_vision=supports_vision,
        )
    ]
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
    ]
    return build_test_vibe_config(
        active_model="test-model", models=models, providers=providers
    )


# ── plain text — guard is never triggered ────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_text_message_passes_through():
    backend = FakeBackend(mock_llm_chunk(content="Hello!"))
    agent = build_test_agent_loop(config=_vision_config(False), backend=backend)
    [e async for e in agent.act("Hello")]
    assert len(backend.requests_messages) == 1
    assert _sent_user_msg(backend).image_parts is None


# ── vision-capable active model — no callback needed ─────────────────────────


@pytest.mark.asyncio
async def test_vision_capable_model_sends_images_without_callback(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)

    backend = FakeBackend(mock_llm_chunk(content="I see a photo."))
    agent = build_test_agent_loop(config=_vision_config(True), backend=backend)

    image_parts = [ImageContentPart(image_url="data:image/png;base64,abc")]
    [_ async for _ in agent.act("describe this", image_parts=image_parts)]

    sent = _sent_user_msg(backend)
    assert sent.image_parts is not None
    assert len(sent.image_parts) == 1


# ── non-vision model, no callback (non-TUI mode) ──────────────────────────────


@pytest.mark.asyncio
async def test_non_vision_model_no_callback_strips_images_silently(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)

    backend = FakeBackend(mock_llm_chunk(content="I see nothing."))
    agent = build_test_agent_loop(config=_vision_config(False), backend=backend)

    image_parts = [ImageContentPart(image_url="data:image/png;base64,abc")]
    [_ async for _ in agent.act("describe this", image_parts=image_parts)]

    assert _sent_user_msg(backend).image_parts is None


# ── callback returns None — task is dropped ───────────────────────────────────


@pytest.mark.asyncio
async def test_vision_guard_drops_task_when_callback_returns_none(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)

    backend = FakeBackend(mock_llm_chunk(content="should not be called"))
    agent = build_test_agent_loop(config=_vision_config(False), backend=backend)

    async def cancel_callback():
        return None

    agent.set_vision_model_callback(cancel_callback)

    image_parts = [ImageContentPart(image_url="data:image/png;base64,abc")]
    [_ async for _ in agent.act("describe this", image_parts=image_parts)]

    assert backend.requests_messages == []


# ── callback returns a vision model — override model is used ─────────────────


@pytest.mark.asyncio
async def test_vision_guard_uses_override_model_from_callback(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)

    backend = FakeBackend(mock_llm_chunk(content="I see the image."))
    agent = build_test_agent_loop(config=_vision_config(False), backend=backend)

    vision_model = ModelConfig(
        name="pixtral-large-2411",
        provider="mistral",
        alias="pixtral-large",
        supports_vision=True,
    )

    async def pick_vision_model():
        return vision_model

    agent.set_vision_model_callback(pick_vision_model)

    image_parts = [ImageContentPart(image_url="data:image/png;base64,abc")]
    [_ async for _ in agent.act("describe this", image_parts=image_parts)]

    assert len(backend.requests_messages) == 1
    assert _sent_user_msg(backend).image_parts is not None


@pytest.mark.asyncio
async def test_active_model_unchanged_after_vision_override():
    backend = FakeBackend([
        mock_llm_chunk(content="vision response"),
        mock_llm_chunk(content="plain response"),
    ])
    agent = build_test_agent_loop(config=_vision_config(False), backend=backend)

    vision_model = ModelConfig(
        name="pixtral-large-2411",
        provider="mistral",
        alias="pixtral-large",
        supports_vision=True,
    )

    async def pick_vision_model():
        return vision_model

    agent.set_vision_model_callback(pick_vision_model)

    image_parts = [ImageContentPart(image_url="data:image/png;base64,abc")]
    [_ async for _ in agent.act("describe this", image_parts=image_parts)]

    # After the vision turn, _current_turn_model must be reset to None
    assert agent._current_turn_model is None

    # A plain follow-up uses the original (non-vision) active model
    [_ async for _ in agent.act("follow-up plain message")]
    assert len(backend.requests_messages) == 2
