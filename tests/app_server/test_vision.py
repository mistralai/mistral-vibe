from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from vibe.app_server import _vision
from vibe.app_server._vision import FALLBACK_EVENT, SessionImageDescriber
from vibe.app_server._workspace import PromptPreparationError
from vibe.app_server.models import (
    ContentBlock,
    FileImageSource,
    ImageAttachment,
    ImageContentBlock,
    SessionContentBlock,
    SessionImageContentBlock,
    SessionTextContentBlock,
    TextContentBlock,
)
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import Backend, LLMChunk, LLMMessage
from vibe.utils.images import MAX_IMAGES_PER_MESSAGE

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

_PROVIDERS = [
    ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    ),
    ProviderConfig(
        name="zhipu",
        api_base="https://open.bigmodel.cn/api/paas/v4",
        api_key_env_var="ZHIPU_API_KEY",
        backend=Backend.GENERIC,
    ),
]

_VISION_MODEL = ModelConfig(
    name="mistral-medium-latest",
    provider="mistral",
    alias="vision",
    supports_images=True,
)


def _config(*, active_sees_images: bool, vision_model: bool) -> VibeConfigSchema:
    models = [
        ModelConfig(
            name="glm-4.6",
            provider="zhipu",
            alias="glm",
            supports_images=active_sees_images,
        )
    ]
    return build_test_vibe_config(
        active_model="glm",
        models=models,
        providers=_PROVIDERS,
        vision_model=_VISION_MODEL if vision_model else None,
    )


class RecordingComplete:
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies) or ["a red error dialog"]
        self.calls: list[Sequence[LLMMessage]] = []

    async def __call__(
        self, *, model: ModelConfig, messages: Sequence[LLMMessage], **_: object
    ) -> LLMChunk:
        self.calls.append(messages)
        return mock_llm_chunk(
            content=self._replies[min(len(self.calls) - 1, len(self._replies) - 1)]
        )


@pytest.fixture
def complete(monkeypatch: pytest.MonkeyPatch) -> RecordingComplete:
    recorder = RecordingComplete()
    monkeypatch.setattr(_vision, "complete_vision", recorder)
    return recorder


def _image_block(tmp_path: Path, name: str = "shot.png") -> ImageContentBlock:
    path = tmp_path / name
    path.write_bytes(PNG_BYTES)
    return ImageContentBlock(
        attachment=ImageAttachment(
            source=FileImageSource(path=str(path)), alias=name, mime_type="image/png"
        )
    )


def _session_image_block(
    tmp_path: Path, name: str = "shot.png"
) -> SessionImageContentBlock:
    path = tmp_path / name
    path.write_bytes(PNG_BYTES)
    return SessionImageContentBlock(
        uri=path.resolve().as_uri(), media_type="image/png", alt_text=name
    )


def _describer(
    config: VibeConfigSchema,
    notices: list[str] | None = None,
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> SessionImageDescriber:
    recorded = events if events is not None else []
    return SessionImageDescriber(
        lambda: config,
        notice=(notices if notices is not None else []).append,
        session_id=lambda: "session-1",
        record_event=lambda name, props: recorded.append((name, props)),
    )


class TestDescribedBlocks:
    @pytest.mark.asyncio
    async def test_image_becomes_a_tagged_description(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        blocks: list[ContentBlock] = [
            TextContentBlock(text="what is the error?"),
            _image_block(tmp_path),
        ]

        described = await _describer(
            _config(active_sees_images=False, vision_model=True)
        ).described_blocks(blocks)

        assert described[0] is blocks[0]
        assert isinstance(described[1], TextContentBlock)
        assert 'alias="shot.png"' in described[1].text
        assert "a red error dialog" in described[1].text

    @pytest.mark.asyncio
    async def test_prompt_steers_the_description(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        await _describer(
            _config(active_sees_images=False, vision_model=True)
        ).described_blocks([
            TextContentBlock(text="what is the error?"),
            _image_block(tmp_path),
        ])

        [messages] = complete.calls
        assert "what is the error?" in (messages[-1].content or "")

    @pytest.mark.asyncio
    async def test_a_model_with_vision_keeps_its_images(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        blocks: list[ContentBlock] = [_image_block(tmp_path)]

        described = await _describer(
            _config(active_sees_images=True, vision_model=True)
        ).described_blocks(blocks)

        assert described is blocks
        assert not complete.calls

    @pytest.mark.asyncio
    async def test_without_a_vision_model_nothing_happens(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        blocks: list[ContentBlock] = [_image_block(tmp_path)]

        described = await _describer(
            _config(active_sees_images=False, vision_model=False)
        ).described_blocks(blocks)

        assert described is blocks
        assert not complete.calls

    @pytest.mark.asyncio
    async def test_a_sibling_on_the_active_provider_needs_no_config(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        config = build_test_vibe_config(
            active_model="blind",
            models=[
                ModelConfig(name="glm-4.6", provider="mistral", alias="blind"),
                ModelConfig(
                    name="mistral-medium-latest",
                    provider="mistral",
                    alias="sees",
                    supports_images=True,
                ),
            ],
            providers=_PROVIDERS,
        )

        described = await _describer(config).described_blocks([_image_block(tmp_path)])

        assert isinstance(described[0], TextContentBlock)
        assert "a red error dialog" in described[0].text

    @pytest.mark.asyncio
    async def test_text_only_input_is_returned_unchanged(
        self, complete: RecordingComplete
    ) -> None:
        blocks: list[ContentBlock] = [TextContentBlock(text="hello")]

        described = await _describer(
            _config(active_sees_images=False, vision_model=True)
        ).described_blocks(blocks)

        assert described is blocks
        assert not complete.calls

    @pytest.mark.asyncio
    async def test_a_failed_description_degrades_to_a_placeholder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(**_: object) -> LLMChunk:
            raise RuntimeError("vision provider is down")

        monkeypatch.setattr(_vision, "complete_vision", boom)
        notices: list[str] = []

        described = await _describer(
            _config(active_sees_images=False, vision_model=True), notices
        ).described_blocks([_image_block(tmp_path)])

        assert isinstance(described[0], TextContentBlock)
        assert "could not be analyzed" in described[0].text
        assert notices == [
            "vision could not describe shot.png: vision provider is down"
        ]

    @pytest.mark.asyncio
    async def test_a_successful_description_says_nothing(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        notices: list[str] = []

        await _describer(
            _config(active_sees_images=False, vision_model=True), notices
        ).described_blocks([_image_block(tmp_path)])

        assert notices == []

    @pytest.mark.asyncio
    async def test_the_same_image_is_described_once_per_session(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        describer = _describer(_config(active_sees_images=False, vision_model=True))
        block = _image_block(tmp_path)

        await describer.described_blocks([block])
        await describer.described_blocks([block])

        assert len(complete.calls) == 1


class TestDescribedSessionBlocks:
    @pytest.mark.asyncio
    async def test_queued_image_becomes_a_tagged_description(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        described = await _describer(
            _config(active_sees_images=False, vision_model=True)
        ).described_session_blocks([
            SessionTextContentBlock(text="look"),
            _session_image_block(tmp_path),
        ])

        assert isinstance(described[1], SessionTextContentBlock)
        assert 'alias="shot.png"' in described[1].text
        assert "a red error dialog" in described[1].text

    @pytest.mark.asyncio
    async def test_a_model_with_vision_keeps_its_queued_images(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        blocks: list[SessionContentBlock] = [_session_image_block(tmp_path)]

        described = await _describer(
            _config(active_sees_images=True, vision_model=True)
        ).described_session_blocks(blocks)

        assert described is blocks
        assert not complete.calls

    @pytest.mark.asyncio
    async def test_an_undescribable_queued_image_is_passed_through(
        self, complete: RecordingComplete
    ) -> None:
        # Reading the attachment out of this block raises: the URI is not
        # local. With no describer there is nothing to read it for, so the
        # block has to reach Core untouched rather than fail the send.
        blocks: list[SessionContentBlock] = [
            SessionImageContentBlock(
                uri="https://example.test/shot.png",
                media_type="image/png",
                alt_text="shot.png",
            )
        ]

        described = await _describer(
            _config(active_sees_images=False, vision_model=False)
        ).described_session_blocks(blocks)

        assert described is blocks
        assert not complete.calls


class TestImageCount:
    @pytest.mark.asyncio
    async def test_too_many_images_is_refused_before_any_provider_call(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        # The CLI and ACP cap a message; the app-server protocol accepts an
        # unbounded block list, so without a check here one turn orders
        # arbitrarily many provider calls.
        blocks: list[ContentBlock] = [
            _image_block(tmp_path, f"{n}.png")
            for n in range(MAX_IMAGES_PER_MESSAGE + 1)
        ]

        with pytest.raises(PromptPreparationError, match="Too many image"):
            await _describer(
                _config(active_sees_images=False, vision_model=True)
            ).described_blocks(blocks)

        assert not complete.calls

    @pytest.mark.asyncio
    async def test_the_cap_holds_for_a_model_that_sees_images(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        # A boundary check, not a describer budget: the blocks go on to Core
        # either way.
        blocks: list[ContentBlock] = [
            _image_block(tmp_path, f"{n}.png")
            for n in range(MAX_IMAGES_PER_MESSAGE + 1)
        ]

        with pytest.raises(PromptPreparationError):
            await _describer(
                _config(active_sees_images=True, vision_model=True)
            ).described_blocks(blocks)

    @pytest.mark.asyncio
    async def test_a_queued_message_is_capped_too(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        blocks: list[SessionContentBlock] = [
            _session_image_block(tmp_path, f"{n}.png")
            for n in range(MAX_IMAGES_PER_MESSAGE + 1)
        ]

        with pytest.raises(PromptPreparationError):
            await _describer(
                _config(active_sees_images=False, vision_model=True)
            ).described_session_blocks(blocks)


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_the_switch_is_recorded_with_both_models(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        events: list[tuple[str, dict[str, Any]]] = []

        await _describer(
            _config(active_sees_images=False, vision_model=True), events=events
        ).described_blocks([_image_block(tmp_path)])

        [(name, props)] = events
        assert name == FALLBACK_EVENT
        assert (props["from"], props["to"]) == ("glm", "vision")
        assert props["outcome"] == "success"
        assert props["nb_images_described"] == 1

    @pytest.mark.asyncio
    async def test_a_failure_is_recorded_as_such(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(**_: object) -> LLMChunk:
            raise RuntimeError("vision provider is down")

        monkeypatch.setattr(_vision, "complete_vision", boom)
        events: list[tuple[str, dict[str, Any]]] = []

        await _describer(
            _config(active_sees_images=False, vision_model=True), events=events
        ).described_blocks([_image_block(tmp_path)])

        [(_, props)] = events
        assert props["outcome"] == "failure"
        assert props["nb_images_failed"] == 1
        # The provider's message reaches the user as a notice; it must not
        # reach the datalake.
        assert "down" not in str(props)

    @pytest.mark.asyncio
    async def test_nothing_is_recorded_when_no_description_happens(
        self, tmp_path: Path, complete: RecordingComplete
    ) -> None:
        events: list[tuple[str, dict[str, Any]]] = []

        await _describer(
            _config(active_sees_images=True, vision_model=True), events=events
        ).described_blocks([_image_block(tmp_path)])

        assert events == []
