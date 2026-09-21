"""Core can already show a blind model something other than the pixels: that
is what ``image_delivery`` set to ``resource_link`` does for a file-backed
attachment. But that projection is synchronous and infallible, and describing
an image is neither -- it is a provider call that takes seconds and can fail.
So the Host substitutes at the turn-input seam instead, where mentioned files
are already inlined and an await with a failure path is available. What is
substituted once is what Core stores, compacts and resumes.

An image no describer can reach is left untouched, and Core's resource-link
projection hands the model the local file in its place -- but only for a
file-backed one, so ``images_supported`` promises nothing on the strength of
that alone (see ``project_config_view``). Only the Unified backend does either;
the legacy ``AgentLoop`` keeps rejecting attachments its model cannot read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.app_server._turn_input import image_attachment_from_session_block
from vibe.app_server._workspace import PromptPreparationError
from vibe.app_server.models import (
    ImageContentBlock,
    SessionImageContentBlock,
    SessionTextContentBlock,
    TextContentBlock,
)
from vibe.core.tracing import build_otel_span_exporter_config
from vibe.core.types import ImageAttachment as CoreImageAttachment
from vibe.core.vision import ImageDescriber, complete_vision
from vibe.utils.images import MAX_IMAGES_PER_MESSAGE

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

    from vibe.app_server.models import (
        ContentBlock,
        ImageAttachment,
        SessionContentBlock,
    )
    from vibe.core.config import ModelConfig, VibeConfigSchema
    from vibe.core.types import LLMChunk, LLMMessage
    from vibe.core.vision import DescribeReport

__all__ = ["SessionImageDescriber"]

UNREADABLE_IMAGE = (
    "The image could not be analyzed. Ask the user to describe what it shows."
)

# The event VIBE-3692 registered for this fallback, extended with the sidecar's
# own dimensions rather than duplicated. Never carries image bytes, prompts,
# descriptions or provider error text.
FALLBACK_EVENT = "vibe.multimodal_fallback_switch"


class SessionImageDescriber:
    def __init__(
        self,
        config: Callable[[], VibeConfigSchema],
        *,
        notice: Callable[[str], None],
        session_id: Callable[[], str | None],
        record_event: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._config = config
        self._notice = notice
        self._session_id = session_id
        self._record_event = record_event
        self._describer = ImageDescriber(self._complete)

    async def described_blocks(self, blocks: list[ContentBlock]) -> list[ContentBlock]:
        images = [block for block in blocks if isinstance(block, ImageContentBlock)]
        if not images:
            return blocks
        _reject_beyond_limit(len(images))
        model = self._config().get_vision_fallback_model()
        if model is None:
            return blocks
        self._report(
            await self._describer.describe_all(
                [_core_attachment(image.attachment) for image in images],
                model=model,
                instruction=" ".join(
                    block.text
                    for block in blocks
                    if isinstance(block, TextContentBlock)
                ),
            ),
            model,
        )
        return [
            TextContentBlock(text=self._image_text(_core_attachment(block.attachment)))
            if isinstance(block, ImageContentBlock)
            else block
            for block in blocks
        ]

    async def described_session_blocks(
        self, blocks: list[SessionContentBlock]
    ) -> list[SessionContentBlock]:
        # Conversion is deferred past the model check: it parses the block's
        # URI and rejects anything non-local, which must not turn a queued
        # image this session was never going to describe into a failed send.
        images = [
            block for block in blocks if isinstance(block, SessionImageContentBlock)
        ]
        if not images:
            return blocks
        _reject_beyond_limit(len(images))
        model = self._config().get_vision_fallback_model()
        if model is None:
            return blocks
        self._report(
            await self._describer.describe_all(
                [_session_core_attachment(image) for image in images],
                model=model,
                instruction=" ".join(
                    block.text
                    for block in blocks
                    if isinstance(block, SessionTextContentBlock)
                ),
            ),
            model,
        )
        return [
            SessionTextContentBlock(
                text=self._image_text(_session_core_attachment(block))
            )
            if isinstance(block, SessionImageContentBlock)
            else block
            for block in blocks
        ]

    def _report(self, report: DescribeReport, model: ModelConfig) -> None:
        # The agent is handed a placeholder either way, so without this the
        # user only sees it claim it cannot see an image it was never shown.
        for failure in report.failures:
            self._notice(
                f"{model.alias} could not describe {failure.alias}: {failure.reason}"
            )
        self._record(report, model)

    def _record(self, report: DescribeReport, model: ModelConfig) -> None:
        try:
            blind = self._config().get_active_model().alias
        except ValueError:
            blind = None
        self._record_event(
            FALLBACK_EVENT,
            {
                "from": blind,
                "to": model.alias,
                "provider": model.provider,
                "outcome": report.outcome,
                "nb_images_described": report.described,
                "nb_images_cached": report.cached,
                "nb_images_failed": len(report.failures),
                "nb_prompt_tokens": report.prompt_tokens,
                "nb_completion_tokens": report.completion_tokens,
                "duration_ms": report.duration_ms,
            },
        )

    def _image_text(self, attachment: CoreImageAttachment) -> str:
        body = self._describer.cached(attachment) or UNREADABLE_IMAGE
        return f'<image alias="{attachment.alias}">\n{body}\n</image>'

    async def _complete(
        self, *, model: ModelConfig, messages: Sequence[LLMMessage]
    ) -> LLMChunk:
        config = self._config()
        return await complete_vision(
            model=model,
            provider=config.get_provider_for_model(model),
            messages=messages,
            timeout=config.api_timeout,
            retry_max_elapsed_time=config.api_retry_max_elapsed_time,
            session_id=self._session_id(),
            enable_otel=_otel_enabled(config),
        )


def _otel_enabled(config: VibeConfigSchema) -> bool:
    # The same gate the legacy loop applies to its own completions: without a
    # resolvable exporter the tracer instruments the call and drops the spans.
    if not (config.enable_telemetry and config.enable_otel):
        return False
    return (
        build_otel_span_exporter_config(
            config.otel_endpoint, config.get_mistral_provider()
        )
        is not None
    )


def _reject_beyond_limit(count: int) -> None:
    # The CLI and ACP both cap a message here, but the app-server protocol
    # accepts an unbounded block list: without this, one turn/start can order
    # arbitrarily many provider calls.
    if count > MAX_IMAGES_PER_MESSAGE:
        raise PromptPreparationError(
            f"Too many image attachments (got {count}, max {MAX_IMAGES_PER_MESSAGE})."
        )


def _core_attachment(attachment: ImageAttachment) -> CoreImageAttachment:
    # by_alias=False: protocol models serialize to camelCase, core types read
    # snake_case.
    return CoreImageAttachment.model_validate(
        attachment.model_dump(mode="json", by_alias=False)
    )


def _session_core_attachment(block: SessionImageContentBlock) -> CoreImageAttachment:
    return _core_attachment(image_attachment_from_session_block(block))
