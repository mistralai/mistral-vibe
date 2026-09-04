from __future__ import annotations

from pathlib import Path
from typing import assert_never
from urllib.parse import urlparse

from vibe.app_server.models import (
    ContentBlock,
    FileImageSource,
    ImageAttachment,
    ImageContentBlock,
    InlineImageSource,
    ResourceContentBlock,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    SessionContentBlock,
    SessionEmbeddedResourceContentBlock,
    SessionImageContentBlock,
    SessionResourceLinkContentBlock,
    SessionTextContentBlock,
)
from vibe.user_content import (
    UserBlobResource,
    UserResource,
    UserResourceLink,
    UserTextResource,
)
from vibe.utils.paths import file_uri_to_path


def session_content_blocks(
    text: str,
    images: list[ImageAttachment] | None,
    resources: list[UserResource] | None = None,
) -> list[SessionContentBlock]:
    return session_content_blocks_from_vibe([
        TextContentBlock(text=text),
        *[ImageContentBlock(attachment=image) for image in images or []],
        *[ResourceContentBlock(resource=resource) for resource in resources or []],
    ])


def session_content_blocks_from_vibe(
    content: list[ContentBlock],
) -> list[SessionContentBlock]:
    return [_session_content_block(block) for block in content]


def vibe_content_blocks(content: list[SessionContentBlock]) -> list[ContentBlock]:
    return [_vibe_content_block(block) for block in content]


def _session_content_block(block: ContentBlock) -> SessionContentBlock:
    match block:
        case TextContentBlock():
            return SessionTextContentBlock(text=block.text)
        case ImageContentBlock():
            return _session_image_content(block.attachment)
        case ResourceContentBlock():
            return _session_resource_content(block.resource)
        case _:
            assert_never(block)


def _session_image_content(image: ImageAttachment) -> SessionImageContentBlock:
    source = image.source
    uri = (
        f"data:{image.mime_type};base64,{source.data}"
        if isinstance(source, InlineImageSource)
        else Path(source.path).expanduser().resolve().as_uri()
    )
    return SessionImageContentBlock(
        uri=uri, media_type=image.mime_type, alt_text=image.alias
    )


def _session_resource_content(
    resource: UserResource,
) -> SessionResourceLinkContentBlock | SessionEmbeddedResourceContentBlock:
    match resource:
        case UserResourceLink():
            return SessionResourceLinkContentBlock(
                uri=resource.uri,
                name=resource.name,
                title=resource.title,
                description=resource.description,
                media_type=resource.media_type,
                size=resource.size,
            )
        case UserTextResource():
            return SessionEmbeddedResourceContentBlock(
                uri=resource.uri, media_type=resource.media_type, text=resource.text
            )
        case UserBlobResource():
            return SessionEmbeddedResourceContentBlock(
                uri=resource.uri, media_type=resource.media_type, blob=resource.blob
            )
        case _:
            assert_never(resource)


def _vibe_content_block(block: SessionContentBlock) -> ContentBlock:
    match block:
        case SessionTextContentBlock():
            return TextContentBlock(text=block.text)
        case SessionImageContentBlock():
            return ImageContentBlock(attachment=_vibe_image_attachment(block))
        case SessionResourceLinkContentBlock():
            return ResourceContentBlock(
                resource=UserResourceLink(
                    uri=block.uri,
                    name=block.name,
                    title=block.title,
                    description=block.description,
                    media_type=block.media_type,
                    size=block.size,
                )
            )
        case SessionEmbeddedResourceContentBlock():
            if block.text is not None:
                return ResourceContentBlock(
                    resource=UserTextResource(
                        uri=block.uri, media_type=block.media_type, text=block.text
                    )
                )
            if block.blob is not None:
                return ResourceContentBlock(
                    resource=UserBlobResource(
                        uri=block.uri, media_type=block.media_type, blob=block.blob
                    )
                )
            raise RuntimeError("Validated embedded resource has no content")
        case _:
            assert_never(block)


def _vibe_image_attachment(block: SessionImageContentBlock) -> ImageAttachment:
    if block.uri.startswith("data:"):
        header, separator, data = block.uri.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ValueError("Queued image URI is not base64 data")
        media_type = block.media_type or header[5:-7]
        if not media_type:
            raise ValueError("Queued image URI has no media type")
        return ImageAttachment(
            source=InlineImageSource(data=data),
            alias=block.alt_text or "image",
            mime_type=media_type,
        )

    parsed = urlparse(block.uri)
    if parsed.scheme not in {"", "file"}:
        raise ValueError(f"Queued image URI is not local: {block.uri!r}")
    path = file_uri_to_path(block.uri) if parsed.scheme == "file" else block.uri
    if block.media_type is None:
        raise ValueError("Queued image URI has no media type")
    return ImageAttachment(
        source=FileImageSource(path=path),
        alias=block.alt_text or Path(path).name or "image",
        mime_type=block.media_type,
    )


__all__ = [
    "session_content_blocks",
    "session_content_blocks_from_vibe",
    "vibe_content_blocks",
]
