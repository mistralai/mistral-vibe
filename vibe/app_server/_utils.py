from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
import time

from pydantic import BaseModel, JsonValue

from vibe.app_server.models import (
    FileImageSource as PublicFileImageSource,
    ImageContentBlock,
    InlineImageSource as PublicInlineImageSource,
    PublicError,
    ResourceContentBlock,
    TextContentBlock,
    TurnErrorCode,
)
from vibe.app_server.protocol import (
    ContextInjectParams,
    TurnStartParams,
    TurnSteerParams,
)
from vibe.core.agent_loop import CompactionFailedError, ImagesNotSupportedError
from vibe.core.llm.exceptions import BackendError, IncompleteStreamError
from vibe.core.session.image_snapshot import ImageSnapshotError, snapshot_image_bytes
from vibe.core.types import (
    ContextTooLongError,
    ImageAttachment,
    RateLimitError,
    RefusalError,
    ResponseTooLongError,
)
from vibe.user_content import UserResource, render_user_resources


@dataclass(frozen=True, slots=True)
class DecodedInput:
    prompt: str
    input_text: str
    images: list[ImageAttachment]
    resources: list[UserResource]


def now_ms() -> int:
    return int(time.time() * 1000)


def decode_input(
    params: TurnStartParams | TurnSteerParams | ContextInjectParams,
    *,
    session_dir: Path | None,
) -> DecodedInput:
    text: list[str] = []
    images: list[ImageAttachment] = []
    resources: list[UserResource] = []
    for block in params.input:
        match block:
            case TextContentBlock():
                text.append(block.text)
            case ImageContentBlock():
                match block.attachment.source:
                    case PublicInlineImageSource(data=data):
                        try:
                            decoded = base64.b64decode(data, validate=True)
                        except (binascii.Error, ValueError) as exc:
                            raise ImageSnapshotError(
                                f"Invalid base64 image data: {exc}"
                            ) from exc
                        images.append(
                            snapshot_image_bytes(
                                decoded,
                                alias=block.attachment.alias,
                                mime_type=block.attachment.mime_type,
                                session_dir=session_dir,
                            )
                        )
                    case PublicFileImageSource():
                        images.append(
                            ImageAttachment.model_validate(
                                block.attachment.model_dump(mode="json", by_alias=False)
                            )
                        )
            case ResourceContentBlock(resource=resource):
                resources.append(resource)
    input_text = "\n".join(text)
    resource_text = render_user_resources(resources)
    return DecodedInput(
        prompt="\n\n".join(part for part in (input_text, resource_text) if part),
        input_text=input_text,
        images=images,
        resources=resources,
    )


def public_error(exc: Exception) -> PublicError:  # noqa: PLR0912
    details: dict[str, JsonValue] = {}
    for source in (exc, exc.__cause__):
        if source is None:
            continue
        for name in ("provider", "model", "category", "explanation"):
            if name in details:
                continue
            value = getattr(source, name, None)
            if isinstance(value, str):
                details[name] = value
    match exc:
        case RateLimitError():
            code = TurnErrorCode.RATE_LIMIT
        case ContextTooLongError():
            code = TurnErrorCode.CONTEXT_TOO_LONG
        case ResponseTooLongError():
            code = TurnErrorCode.RESPONSE_TOO_LONG
        case RefusalError():
            code = TurnErrorCode.REFUSAL
        case ImageSnapshotError():
            code = TurnErrorCode.INVALID_IMAGE_ATTACHMENT
        case ImagesNotSupportedError():
            code = TurnErrorCode.IMAGES_NOT_SUPPORTED
        case CompactionFailedError():
            code = TurnErrorCode.COMPACTION_FAILED
            details["reason"] = exc.reason
        case IncompleteStreamError():
            code = TurnErrorCode.INCOMPLETE_STREAM
        case BackendError() if exc.is_invalid_model:
            code = TurnErrorCode.INVALID_MODEL
        case BackendError():
            code = TurnErrorCode.BACKEND_ERROR
        case RuntimeError() if (
            isinstance(cause := exc.__cause__, BackendError) and cause.is_invalid_model
        ):
            code = TurnErrorCode.INVALID_MODEL
        case RuntimeError() if isinstance(cause := exc.__cause__, BackendError):
            code = TurnErrorCode.BACKEND_ERROR
            details["provider"] = cause.provider
            details["model"] = cause.model
        case _:
            code = TurnErrorCode.INTERNAL_ERROR
    return PublicError(message=str(exc), code=code, details=details or None)


def dump_model(model: BaseModel) -> dict[str, JsonValue]:
    return model.model_dump(mode="json", by_alias=True)
