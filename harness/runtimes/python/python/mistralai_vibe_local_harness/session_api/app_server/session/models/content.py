"""Public content blocks retained in Session history."""

import base64
import os
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import (
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    ModelWrapValidatorHandler,
    PrivateAttr,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    Tag,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from .base import SessionModel, read_unknown_variant, write_unknown_variant
from .common import Uri

# Open: a newer Runtime may name an audience this reader does not know yet.
type ContentAudience = Literal["user", "assistant"] | str

MAX_MEDIA_BASE64_CHARS = 10_000_000
"""Longest base64 data, in characters, of an image ``ImageContentBlock.from_path`` reads."""
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class ContentAnnotations(SessionModel):
    """MCP block annotations: who a block is for and how much it matters.

    A tool result block whose audience excludes ``"assistant"`` is never sent to the
    model; a client may hide a block whose audience excludes ``"user"``. A reader keeps an
    audience it does not know and reads a priority outside 0 to 1 as none, so that a newer
    Runtime's annotations do not fail the block. A request refuses both.
    """

    audience: list[ContentAudience] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    priority: float | None = Field(
        default=None,
        ge=0,
        le=1,
        exclude_if=lambda value: value is None,
        description="Read as none when outside 0 to 1. A request refuses such a value.",
    )
    last_modified: str | None = Field(default=None, exclude_if=lambda value: value is None)
    # Kept for refuse_unsupported_blocks, so a request still refuses what a reader drops.
    _dropped_priority: float | None = PrivateAttr(default=None)

    @model_validator(mode="wrap")
    @classmethod
    def _drop_out_of_range_priority(
        cls, value: object, handler: ModelWrapValidatorHandler["ContentAnnotations"]
    ) -> "ContentAnnotations":
        priority = value.get("priority") if isinstance(value, dict) else None
        # Out of range breaks the MCP spec, so only a faulty producer sends it: dropped, so
        # that it does not fail the block.
        out_of_range = isinstance(priority, int | float) and not 0 <= priority <= 1
        if out_of_range:
            assert isinstance(value, dict)
            value = {key: item for key, item in value.items() if key != "priority"}
        annotations = handler(value)
        if out_of_range:
            annotations._dropped_priority = priority
        return annotations


class _ContentBlockBase(SessionModel):
    annotations: ContentAnnotations | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    meta: dict[str, JsonValue] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Host metadata, MCP `_meta`. Never shown to the model. Read as none when it is not "
            "an object. A request refuses such a value."
        ),
    )
    # Kept for refuse_unsupported_blocks, so a request still refuses what a reader drops.
    _dropped_meta: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    @classmethod
    def _drop_non_object_meta(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        meta = value.get("meta") if isinstance(value, dict) else None
        # A non-object breaks the MCP spec, so only a faulty producer sends it: dropped, so
        # that it does not fail the block.
        dropped = meta is not None and not isinstance(meta, dict)
        if dropped:
            assert isinstance(value, dict)
            value = {key: item for key, item in value.items() if key != "meta"}
        block = handler(value)
        if dropped:
            block._dropped_meta = True
        return block


class TextContentBlock(_ContentBlockBase):
    type: Literal["text"] = "text"
    text: str = ""


class ImageContentBlock(_ContentBlockBase):
    type: Literal["image"] = "image"
    uri: Uri
    media_type: str | None = None
    alt_text: str | None = None

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        media_type: str | None = None,
        *,
        alt_text: str | None = None,
        annotations: ContentAnnotations | None = None,
    ) -> "ImageContentBlock":
        """An inline image; the media type is read from PNG, JPEG, GIF or WebP bytes."""
        resolved = media_type or sniff_image_media_type(data)
        if resolved is None:
            raise ValueError("cannot tell the image type from its bytes; pass media_type")
        return cls(
            uri=f"data:{resolved};base64,{base64.b64encode(data).decode('ascii')}",
            media_type=resolved,
            alt_text=alt_text,
            annotations=annotations,
        )

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        media_type: str | None = None,
        *,
        alt_text: str | None = None,
        annotations: ContentAnnotations | None = None,
    ) -> "ImageContentBlock":
        """An inline image read from a local file whose base64 is at most
        ``MAX_MEDIA_BASE64_CHARS`` characters."""
        # Base64 takes 4 characters for every 3 bytes, so this is the largest file whose
        # base64 fits the limit.
        max_bytes = MAX_MEDIA_BASE64_CHARS // 4 * 3
        with Path(path).open("rb") as file:
            data = file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(
                f"image file {path} is over the {MAX_MEDIA_BASE64_CHARS:,} base64-character limit"
            )
        return cls.from_bytes(data, media_type, alt_text=alt_text, annotations=annotations)


class AudioContentBlock(_ContentBlockBase):
    type: Literal["audio"] = "audio"
    uri: Uri
    media_type: str | None = None


class ResourceLinkContentBlock(_ContentBlockBase):
    type: Literal["resource_link"] = "resource_link"
    uri: Uri
    name: str | None = None
    title: str | None = None
    description: str | None = None
    media_type: str | None = None
    size: Annotated[int | None, Field(ge=0)] = None


class EmbeddedResourceContentBlock(_ContentBlockBase):
    type: Literal["embedded_resource"] = "embedded_resource"
    uri: Uri
    media_type: str | None = None
    text: str | None = None
    blob: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "EmbeddedResourceContentBlock":
        if (self.text is None) == (self.blob is None):
            raise ValueError("Embedded resources require exactly one of text or blob")
        return self


def sniff_image_media_type(data: bytes) -> str | None:
    """The media type of PNG, JPEG, GIF or WebP bytes, else ``None``."""
    for signature, media_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class UnknownContentBlock(SessionModel):
    """A content block of a type this reader does not know.

    A newer Runtime can add block types. Such a block is kept, so that it does not fail
    the whole message, history page or event that holds it. Its type is saved in
    ``original_type`` and its other fields, ``annotations`` and ``meta`` included, are
    kept as they came. Its own ``type`` is ``unknown``, so a ``match`` on ``type`` still
    works for the known blocks. Serialized, it is the block as it came: its own ``type``,
    no ``original_type``. Blocks of a known type are validated as before. A Turn cannot
    run such a block: requests refuse it.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["unknown"] = "unknown"
    original_type: str

    @model_validator(mode="before")
    @classmethod
    def wrap_wire_block(cls, value: object) -> object:
        return read_unknown_variant(value)

    @model_serializer(mode="wrap")
    def write_wire_block(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        return write_unknown_variant(handler(self), self.original_type, self.model_extra, info)


def _content_block_variant(value: object) -> str | None:
    block_type = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    # A missing or non-string type is malformed, not unknown: no variant accepts it.
    if not isinstance(block_type, str):
        return None
    return block_type if block_type in _CONTENT_BLOCK_TYPES else "unknown"


type ContentBlock = Annotated[
    Annotated[TextContentBlock, Tag("text")]
    | Annotated[ImageContentBlock, Tag("image")]
    | Annotated[AudioContentBlock, Tag("audio")]
    | Annotated[ResourceLinkContentBlock, Tag("resource_link")]
    | Annotated[EmbeddedResourceContentBlock, Tag("embedded_resource")]
    | Annotated[UnknownContentBlock, Tag("unknown")],
    Discriminator(_content_block_variant),
]

# The tags of the union's variants, so a new block type is known once it joins the union.
_CONTENT_BLOCK_TYPES: frozenset[str] = frozenset(
    metadata.tag
    for variant in get_args(get_args(ContentBlock.__value__)[0])
    for metadata in variant.__metadata__
    if isinstance(metadata, Tag) and metadata.tag != "unknown"
)


# TODO: remove with the guard on UserInputCallback.message.
UNSUPPORTED_CONTENT_ERROR = "unsupported_content_block"
"""Error type of a reader model that refuses a block an older reader cannot read either, so
that ``ClientEvent`` reads the event as unknown, as it did before ``UnknownContentBlock``."""


def refuse_unsupported_blocks(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """Return ``blocks``; raise ``ValueError`` if a Turn cannot run one of them.

    A reader keeps a block of a type it does not know and an audience it does not know,
    and drops a priority outside 0 to 1 and a ``meta`` that is not an object, but a request
    must only send what a Turn can run.
    """
    for block in blocks:
        if isinstance(block, UnknownContentBlock):
            raise ValueError(f"content block type {block.original_type!r} is not supported")
        if block._dropped_meta:
            raise ValueError("content block meta must be an object")
        if block.annotations is None:
            continue
        for audience in block.annotations.audience or []:
            if audience not in _KNOWN_AUDIENCES:
                raise ValueError(f"content audience {audience!r} is not supported")
        priority = block.annotations._dropped_priority
        if priority is not None:
            raise ValueError(f"content priority {priority:g} is not between 0 and 1")
    return blocks


_KNOWN_AUDIENCES: frozenset[str] = frozenset(get_args(get_args(ContentAudience.__value__)[0]))


ContentBlockAdapter: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)
