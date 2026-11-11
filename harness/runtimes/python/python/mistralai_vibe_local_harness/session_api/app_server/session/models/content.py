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
    """Who the block is for: ``"user"``, ``"assistant"`` or both. ``None`` for both."""
    priority: float | None = Field(
        default=None,
        ge=0,
        le=1,
        exclude_if=lambda value: value is None,
        description="How much the block matters. Must be between 0 and 1.",
    )
    last_modified: str | None = Field(default=None, exclude_if=lambda value: value is None)
    """When the block's content last changed, as an ISO 8601 timestamp."""
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
    """Who the block is for and how much it matters, as MCP annotations."""
    meta: dict[str, JsonValue] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Metadata for your client, as MCP `_meta`. The model never sees it. "
            "Must be a JSON object."
        ),
    )
    # Kept for refuse_unsupported_blocks, so a request still refuses what a reader drops.
    _dropped_meta: bool = PrivateAttr(default=False)
    # The Runtime block this one was read from, for a Hook's view of the Turn: handed back
    # unchanged, the block becomes that Runtime block again, as it was, so a Hook passing
    # content through never changes what the model receives. Set by
    # ``to_session_content(keep_origin=True)``.
    _core_origin: object | None = PrivateAttr(default=None)

    def __eq__(self, other: object) -> bool:
        # The origin is where the block came from, not its value: a block of a Hook's view
        # equals the same block built by hand.
        if not isinstance(other, _ContentBlockBase):
            return NotImplemented
        return (
            type(self) is type(other)
            and self.__dict__ == other.__dict__
            and self.__pydantic_extra__ == other.__pydantic_extra__
            and self._dropped_meta == other._dropped_meta
        )

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
    """The text."""


class ImageContentBlock(_ContentBlockBase):
    type: Literal["image"] = "image"
    uri: Uri
    """The image's URI, such as a ``data:`` URI that holds the image in base64."""
    media_type: str | None = None
    """The image's media type, such as ``"image/png"``."""
    alt_text: str | None = None
    """A text description of the image."""

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        media_type: str | None = None,
        *,
        alt_text: str | None = None,
        annotations: ContentAnnotations | None = None,
    ) -> "ImageContentBlock":
        """An inline image; the media type is read from PNG, JPEG, GIF or WebP bytes.

        Args:
            data: The image's bytes.
            media_type: The image's media type. Defaults to the one read from the bytes.
            alt_text: A text description of the image.
            annotations: Who the block is for and how much it matters.
        """
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
        ``MAX_MEDIA_BASE64_CHARS`` characters.

        Args:
            path: The image file.
            media_type: The image's media type. Defaults to the one read from the file's bytes.
            alt_text: A text description of the image.
            annotations: Who the block is for and how much it matters.
        """
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
    """The clip's URI, such as a ``data:`` URI that holds the clip in base64."""
    media_type: str | None = None
    """The clip's media type, such as ``"audio/wav"``."""


class ResourceLinkContentBlock(_ContentBlockBase):
    type: Literal["resource_link"] = "resource_link"
    uri: Uri
    """The resource's URI."""
    name: str | None = None
    """The resource's name."""
    title: str | None = None
    """A name for people."""
    description: str | None = None
    """What the resource is."""
    media_type: str | None = None
    """The resource's media type."""
    size: Annotated[int | None, Field(ge=0)] = None
    """The resource's size, in bytes."""


class EmbeddedResourceContentBlock(_ContentBlockBase):
    type: Literal["embedded_resource"] = "embedded_resource"
    uri: Uri
    """The resource's URI."""
    media_type: str | None = None
    """The resource's media type."""
    text: str | None = None
    """The resource's text. Set exactly one of ``text`` and ``blob``."""
    blob: str | None = None
    """The resource's data, in base64. Set exactly one of ``text`` and ``blob``."""

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
    """A content block of a type added after your SDK version.

    Such a block is kept, so that it does not fail the whole message, history page or event
    that holds it. Its type is saved in ``original_type`` and its other fields,
    ``annotations`` and ``meta`` included, are kept as they came. Its own ``type`` is
    ``unknown``, so a ``match`` on ``type`` still works for the known blocks. Serialized, it
    is the block as it came: its own ``type``, no ``original_type``. Blocks of a known type
    are validated as before. A Turn cannot run such a block: requests refuse it.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["unknown"] = "unknown"
    original_type: str
    """The block's type as it came."""

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


def refuse_unsupported_blocks(
    blocks: list[ContentBlock], *, refuse_audio: bool = True
) -> list[ContentBlock]:
    """Return ``blocks``; raise ``ValueError`` if a Turn cannot run one of them.

    A reader keeps a block of a type it does not know, audio, and an audience it does not
    know, and drops a priority outside 0 to 1 and a ``meta`` that is not an object, but a
    request must only send what a Turn can run. No completion provider takes audio yet.
    ``refuse_audio=False`` leaves audio to the Turn, for content this check accepted it in
    before, such as a tool result or a client tool answer.
    """
    for block in blocks:
        if isinstance(block, UnknownContentBlock):
            raise ValueError(f"content block type {block.original_type!r} is not supported")
        if block._core_origin is not None:
            # Read from the Turn, so the Turn runs it; ``to_core_content`` refuses it if changed.
            continue
        if refuse_audio and isinstance(block, AudioContentBlock):
            raise ValueError(AUDIO_NOT_SUPPORTED)
        if block._dropped_meta:
            raise ValueError("content block meta must be an object")
        refusal = _annotations_refusal(block.annotations)
        if refusal is not None:
            raise ValueError(refusal)
    return blocks


AUDIO_NOT_SUPPORTED = "audio content is not supported yet"


def drop_refused_metadata(block: ContentBlock) -> ContentBlock:
    """Return ``block`` without the annotations or ``meta`` that ``refuse_unsupported_blocks``
    refuses, for a client that dropped them before the Session checked them."""
    if isinstance(block, UnknownContentBlock):
        return block
    update: dict[str, object] = {}
    if _annotations_refusal(block.annotations) is not None:
        update["annotations"] = None
    if not update and not block._dropped_meta:
        return block
    kept = block.model_copy(update=update)
    kept._dropped_meta = False
    return kept


def _annotations_refusal(annotations: ContentAnnotations | None) -> str | None:
    if annotations is None:
        return None
    for audience in annotations.audience or []:
        if audience not in _KNOWN_AUDIENCES:
            return f"content audience {audience!r} is not supported"
    priority = annotations._dropped_priority
    if priority is not None:
        return f"content priority {priority:g} is not between 0 and 1"
    return None


_KNOWN_AUDIENCES: frozenset[str] = frozenset(get_args(get_args(ContentAudience.__value__)[0]))


ContentBlockAdapter: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)
