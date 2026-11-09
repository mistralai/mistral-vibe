from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import assert_never

from acp.helpers import ContentBlock as AcpContentBlock
from acp.schema import (
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)

from vibe.acp.exceptions import InvalidRequestError
from vibe.acp.image_blocks import extract_image_attachments
from vibe.app_server.models import (
    ContentBlock as AppServerContentBlock,
    ImageAttachment,
    ResourceContentBlock as AppServerResourceContentBlock,
    TextContentBlock as AppServerTextContentBlock,
)
from vibe.user_content import (
    UserBlobResource,
    UserResource,
    UserResourceLink,
    UserTextResource,
)


@dataclass(frozen=True, slots=True)
class ProjectedPrompt:
    text: str
    images: list[ImageAttachment]
    resources: list[UserResource]
    title_content: list[AppServerContentBlock]


def project_prompt(blocks: Sequence[AcpContentBlock]) -> ProjectedPrompt:
    automatic = [
        block
        for block in blocks
        if block.field_meta and block.field_meta.get("automatic")
    ]
    ordered = [block for block in blocks if block not in automatic] + automatic
    text: list[str] = []
    resources: list[UserResource] = []
    title_content: list[AppServerContentBlock] = []

    for block in ordered:
        include_in_title = block not in automatic
        match block:
            case TextContentBlock():
                text.append(block.text)
                if include_in_title:
                    title_content.append(AppServerTextContentBlock(text=block.text))
            case EmbeddedResourceContentBlock():
                resource = _project_embedded_resource(block)
                resources.append(resource)
                if include_in_title:
                    title_content.append(
                        AppServerResourceContentBlock(resource=resource)
                    )
            case ResourceContentBlock():
                resource = UserResourceLink(
                    uri=block.uri,
                    name=block.name,
                    title=block.title,
                    description=block.description,
                    media_type=block.mime_type,
                    size=block.size,
                )
                resources.append(resource)
                if include_in_title:
                    title_content.append(
                        AppServerResourceContentBlock(resource=resource)
                    )
            case ImageContentBlock():
                continue
            case _:
                raise InvalidRequestError(
                    f"Unsupported ACP content block: {block.type}"
                )

    return ProjectedPrompt(
        text="\n\n".join(text),
        images=extract_image_attachments(ordered),
        resources=resources,
        title_content=title_content,
    )


def _project_embedded_resource(
    block: EmbeddedResourceContentBlock,
) -> UserTextResource | UserBlobResource:
    match block.resource:
        case TextResourceContents():
            return UserTextResource(
                uri=block.resource.uri,
                media_type=block.resource.mime_type,
                text=block.resource.text,
            )
        case BlobResourceContents():
            return UserBlobResource(
                uri=block.resource.uri,
                media_type=block.resource.mime_type,
                blob=block.resource.blob,
            )
        case _:
            assert_never(block.resource)
