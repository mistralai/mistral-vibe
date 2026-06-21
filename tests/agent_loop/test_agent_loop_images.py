from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import ImagesNotSupportedError
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.types import Backend, FileImageSource, ImageAttachment, LLMMessage, Role

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _config_with_vision_flag(*, supports_images: bool) -> VibeConfig:
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest",
            provider="mistral",
            alias="devstral-latest",
            supports_images=supports_images,
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
        active_model="devstral-latest", models=models, providers=providers
    )


def _config_with_both_models() -> VibeConfig:
    models = [
        ModelConfig(
            name="vision-model",
            provider="mistral",
            alias="vision-alias",
            supports_images=True,
        ),
        ModelConfig(
            name="text-model",
            provider="mistral",
            alias="text-alias",
            supports_images=False,
        ),
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
        active_model="vision-alias", models=models, providers=providers
    )


@pytest.fixture()
def png_attachment(tmp_path: Path) -> ImageAttachment:
    p = tmp_path / "x.png"
    p.write_bytes(PNG_BYTES)
    return ImageAttachment(
        source=FileImageSource(path=p), alias="x.png", mime_type="image/png"
    )


@pytest.mark.asyncio
async def test_act_raises_when_model_lacks_vision(
    png_attachment: ImageAttachment,
) -> None:
    config = _config_with_vision_flag(supports_images=False)
    backend = FakeBackend([mock_llm_chunk(content="ok")])
    agent = build_test_agent_loop(config=config, backend=backend)
    initial_message_count = len(agent.messages)

    with pytest.raises(ImagesNotSupportedError):
        async for _ in agent.act("look", images=[png_attachment]):
            pass

    assert backend.requests_extra_headers == []  # no LLM call was made
    # Capability check runs *before* checkpoint creation and history mutation,
    # so a rejected turn leaves no trace in either.
    assert agent.rewind_manager.checkpoints == []
    assert len(agent.messages) == initial_message_count


@pytest.mark.asyncio
async def test_act_attaches_images_to_user_message(
    png_attachment: ImageAttachment,
) -> None:
    config = _config_with_vision_flag(supports_images=True)
    backend = FakeBackend([mock_llm_chunk(content="ok")])
    agent = build_test_agent_loop(config=config, backend=backend)

    [_ async for _ in agent.act("look", images=[png_attachment])]

    user_msgs = [m for m in agent.messages if m.role.value == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].images == [png_attachment]


def _seed_history_image(agent, png_attachment: ImageAttachment) -> None:
    agent.messages.append(
        LLMMessage(role=Role.user, content="earlier", images=[png_attachment])
    )
    agent.messages.append(LLMMessage(role=Role.assistant, content="seen"))


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_streaming", [False, True])
async def test_history_images_stripped_from_backend_payload_on_non_vision_model(
    png_attachment: ImageAttachment, enable_streaming: bool
) -> None:
    config = _config_with_both_models()
    backend = FakeBackend([mock_llm_chunk(content="ok")])
    agent = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=enable_streaming
    )
    _seed_history_image(agent, png_attachment)
    agent.config.active_model = "text-alias"

    [_ async for _ in agent.act("hi")]

    sent_messages = backend.requests_messages[-1]
    assert all(m.images is None for m in sent_messages)
    # source-of-truth history is not mutated
    assert any(m.images == [png_attachment] for m in agent.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_streaming", [False, True])
async def test_switch_back_to_vision_model_restores_images_in_payload(
    png_attachment: ImageAttachment, enable_streaming: bool
) -> None:
    config = _config_with_both_models()
    backend = FakeBackend([
        [mock_llm_chunk(content="a")],
        [mock_llm_chunk(content="b")],
    ])
    agent = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=enable_streaming
    )
    _seed_history_image(agent, png_attachment)

    agent.config.active_model = "text-alias"
    [_ async for _ in agent.act("hi")]
    agent.config.active_model = "vision-alias"
    [_ async for _ in agent.act("hi again")]

    sent_messages = backend.requests_messages[-1]
    assert any(m.images == [png_attachment] for m in sent_messages)


def test_count_history_images_unsupported_by_active_model(
    png_attachment: ImageAttachment,
) -> None:
    config = _config_with_both_models()
    agent = build_test_agent_loop(config=config)

    assert agent.count_history_images_unsupported_by_active_model() == 0

    _seed_history_image(agent, png_attachment)
    # active model still supports images
    assert agent.count_history_images_unsupported_by_active_model() == 0

    agent.config.active_model = "text-alias"
    assert agent.count_history_images_unsupported_by_active_model() == 1

    agent.messages.append(
        LLMMessage(role=Role.user, content="more", images=[png_attachment])
    )
    assert agent.count_history_images_unsupported_by_active_model() == 2


@pytest.mark.asyncio
async def test_new_images_with_non_vision_model_still_raises(
    png_attachment: ImageAttachment,
) -> None:
    config = _config_with_both_models()
    backend = FakeBackend([mock_llm_chunk(content="ok")])
    agent = build_test_agent_loop(config=config, backend=backend)
    agent.config.active_model = "text-alias"

    with pytest.raises(ImagesNotSupportedError):
        async for _ in agent.act("look", images=[png_attachment]):
            pass
