"""Core content blocks as public history shows them."""

from __future__ import annotations

from typing import Any, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import RustImageContentBlock
from mistralai_vibe_local_harness.vibe._file_images import is_file_image


def public_content_block(part: Any) -> JsonValue:
    """A Core content block as public history shows it."""
    serialized = part.model_dump(mode="json", by_alias=True, exclude_none=True)
    # An image kept with its file shows as the image it was sent as.
    if isinstance(part, RustImageContentBlock) or is_file_image(part):
        data, mime_type = (
            (part.data, part.mime_type)
            if isinstance(part, RustImageContentBlock)
            else (part.resource.blob, part.resource.mime_type)
        )
        return cast(
            JsonValue,
            {
                "type": "image",
                "attachment": {
                    "source": {"kind": "inline", "data": data},
                    "alias": "image",
                    "mimeType": mime_type,
                },
            },
        )
    if isinstance(serialized, dict) and serialized.get("type") == "resource_link":
        return cast(
            JsonValue,
            {
                "type": "resource",
                "resource": {
                    "kind": "link",
                    "uri": serialized["uri"],
                    **({"name": serialized["name"]} if serialized.get("name") else {}),
                    **(
                        {"title": serialized["title"]}
                        if serialized.get("title")
                        else {}
                    ),
                    **(
                        {"description": serialized["description"]}
                        if serialized.get("description")
                        else {}
                    ),
                    **(
                        {"mediaType": serialized["mimeType"]}
                        if serialized.get("mimeType")
                        else {}
                    ),
                    **(
                        {"size": serialized["size"]}
                        if serialized.get("size") is not None
                        else {}
                    ),
                },
            },
        )
    if isinstance(serialized, dict) and serialized.get("type") == "resource":
        resource = serialized.get("resource")
        if isinstance(resource, dict):
            return cast(
                JsonValue,
                {
                    "type": "resource",
                    "resource": {
                        "kind": "text" if "text" in resource else "blob",
                        "uri": resource["uri"],
                        **(
                            {"mediaType": resource["mimeType"]}
                            if resource.get("mimeType")
                            else {}
                        ),
                        **({"text": resource["text"]} if "text" in resource else {}),
                        **({"blob": resource["blob"]} if "blob" in resource else {}),
                    },
                },
            )
    if isinstance(serialized, dict):
        serialized.pop("_meta", None)
    return cast(JsonValue, serialized)


__all__ = ["public_content_block"]
