from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.mock.utils import mock_llm_chunk
from vibe.core.config import ModelConfig
from vibe.core.llm.exceptions import BackendError, PayloadSummary
from vibe.core.types import (
    FileImageSource,
    ImageAttachment,
    InlineImageSource,
    LLMChunk,
    LLMMessage,
    Role,
)
from vibe.core.vision import (
    MAX_CONCURRENT_DESCRIPTIONS,
    FailedDescription,
    ImageDescriber,
    attachment_key,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

VISION_MODEL = ModelConfig(
    name="vision-model", provider="mistral", alias="vision", supports_images=True
)


class RecordingComplete:
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies) or ["a description"]
        self.calls: list[tuple[ModelConfig, Sequence[LLMMessage]]] = []

    async def __call__(
        self, *, model: ModelConfig, messages: Sequence[LLMMessage]
    ) -> LLMChunk:
        self.calls.append((model, messages))
        reply = self._replies[min(len(self.calls) - 1, len(self._replies) - 1)]
        if reply == "__raise__":
            raise RuntimeError("vision provider is down")
        return mock_llm_chunk(content=reply)


def _png(tmp_path: Path, name: str = "x.png") -> ImageAttachment:
    p = tmp_path / name
    p.write_bytes(PNG_BYTES)
    return ImageAttachment(
        source=FileImageSource(path=p), alias=name, mime_type="image/png"
    )


class TestAttachmentKey:
    def test_file_key_tracks_content_changes(self, tmp_path: Path) -> None:
        att = _png(tmp_path)
        before = attachment_key(att)
        (tmp_path / "x.png").write_bytes(PNG_BYTES + b"more")
        assert attachment_key(att) != before

    def test_inline_key_is_content_addressed(self) -> None:
        def inline(data: str) -> ImageAttachment:
            return ImageAttachment(
                source=InlineImageSource(data=data),
                alias="pasted.png",
                mime_type="image/png",
            )

        assert attachment_key(inline("AAAA")) == attachment_key(inline("AAAA"))
        assert attachment_key(inline("AAAA")) != attachment_key(inline("BBBB"))

    def test_missing_file_does_not_raise(self, tmp_path: Path) -> None:
        att = ImageAttachment(
            source=FileImageSource(path=tmp_path / "gone.png"),
            alias="gone.png",
            mime_type="image/png",
        )
        assert attachment_key(att)


class TestImageDescriber:
    @pytest.mark.asyncio
    async def test_describes_and_caches(self, tmp_path: Path) -> None:
        complete = RecordingComplete("a red error dialog")
        describer = ImageDescriber(complete)
        att = _png(tmp_path)

        await describer.describe_all([att], model=VISION_MODEL)
        assert describer.cached(att) == "a red error dialog"

        await describer.describe_all([att], model=VISION_MODEL)
        assert len(complete.calls) == 1

    @pytest.mark.asyncio
    async def test_deduplicates_within_one_call(self, tmp_path: Path) -> None:
        complete = RecordingComplete()
        att = _png(tmp_path)

        await ImageDescriber(complete).describe_all([att, att, att], model=VISION_MODEL)

        assert len(complete.calls) == 1

    @pytest.mark.asyncio
    async def test_instruction_steers_the_request(self, tmp_path: Path) -> None:
        complete = RecordingComplete()

        await ImageDescriber(complete).describe_all(
            [_png(tmp_path)], model=VISION_MODEL, instruction="what is the  error?"
        )

        _, messages = complete.calls[0]
        user = messages[-1]
        assert user.role is Role.user
        assert "what is the error?" in (user.content or "")
        assert user.images is not None and len(user.images) == 1
        assert messages[0].role is Role.system

    @pytest.mark.asyncio
    async def test_failure_leaves_no_cache_entry(self, tmp_path: Path) -> None:
        att = _png(tmp_path)
        describer = ImageDescriber(RecordingComplete("__raise__"))

        await describer.describe_all([att], model=VISION_MODEL)

        assert describer.cached(att) is None

    @pytest.mark.asyncio
    async def test_one_failure_does_not_lose_the_others(self, tmp_path: Path) -> None:
        first = _png(tmp_path, "first.png")
        second = _png(tmp_path, "second.png")
        describer = ImageDescriber(RecordingComplete("__raise__", "second is fine"))

        await describer.describe_all([first, second], model=VISION_MODEL)

        assert describer.cached(second) == "second is fine"

    @pytest.mark.asyncio
    async def test_empty_description_is_not_cached(self, tmp_path: Path) -> None:
        att = _png(tmp_path)
        describer = ImageDescriber(RecordingComplete("   "))

        await describer.describe_all([att], model=VISION_MODEL)

        assert describer.cached(att) is None

    @pytest.mark.asyncio
    async def test_a_failure_is_reported_against_its_image(
        self, tmp_path: Path
    ) -> None:
        describer = ImageDescriber(RecordingComplete("__raise__", "second is fine"))

        report = await describer.describe_all(
            [_png(tmp_path, "first.png"), _png(tmp_path, "second.png")],
            model=VISION_MODEL,
        )

        assert [(f.alias, f.reason) for f in report.failures] == [
            ("first.png", "vision provider is down")
        ]

    @pytest.mark.asyncio
    async def test_an_empty_response_reports_why_it_stopped(
        self, tmp_path: Path
    ) -> None:
        describer = ImageDescriber(RecordingComplete("   "))

        report = await describer.describe_all([_png(tmp_path)], model=VISION_MODEL)

        assert "stop reason" in report.failures[0].reason


class TestDescribeReport:
    @pytest.mark.asyncio
    async def test_counts_and_usage_are_reported(self, tmp_path: Path) -> None:
        # Without this the feature bills provider calls that no dashboard can
        # see; the report is the only place the usage survives.
        describer = ImageDescriber(RecordingComplete("first", "__raise__"))

        report = await describer.describe_all(
            [_png(tmp_path, "a.png"), _png(tmp_path, "b.png")], model=VISION_MODEL
        )

        assert (report.described, len(report.failures)) == (1, 1)
        assert report.outcome == "partial"
        assert report.prompt_tokens > 0

    @pytest.mark.asyncio
    async def test_a_cache_hit_is_counted_not_redescribed(self, tmp_path: Path) -> None:
        att = _png(tmp_path)
        describer = ImageDescriber(RecordingComplete())
        await describer.describe_all([att], model=VISION_MODEL)

        report = await describer.describe_all([att], model=VISION_MODEL)

        assert (report.cached, report.described) == (1, 0)
        assert report.outcome == "success"

    @pytest.mark.asyncio
    async def test_concurrency_stays_within_the_bound(self, tmp_path: Path) -> None:
        # One provider call per image, so an unbounded gather turns a single
        # message into arbitrary provider fan-out.
        peak = 0
        live = 0

        async def complete(
            *, model: ModelConfig, messages: Sequence[LLMMessage]
        ) -> LLMChunk:
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0)
            live -= 1
            return mock_llm_chunk(content="a description")

        await ImageDescriber(complete).describe_all(
            [
                _png(tmp_path, f"{n}.png")
                for n in range(MAX_CONCURRENT_DESCRIPTIONS * 3)
            ],
            model=VISION_MODEL,
        )

        assert peak <= MAX_CONCURRENT_DESCRIPTIONS


class TestFailedDescriptionReason:
    def test_a_backend_error_reduces_to_the_provider_message(self) -> None:
        # BackendError renders as a multi-line diagnostic block; a notice has
        # room for the one line that says what to fix.
        error = BackendError(
            provider="mistral",
            endpoint="https://api.mistral.ai",
            status=400,
            reason="Bad Request",
            headers={},
            body_text='{"message":"top_p must be 1 when using greedy sampling."}',
            parsed_error="top_p must be 1 when using greedy sampling.",
            model="mistral-vibe-cli-latest",
            payload_summary=PayloadSummary(
                model="mistral-vibe-cli-latest",
                message_count=2,
                approx_chars=1448,
                temperature=0.0,
                has_tools=False,
                tool_choice=None,
            ),
        )

        reason = FailedDescription(alias="shot.png", error=error).reason

        assert reason == "top_p must be 1 when using greedy sampling."
