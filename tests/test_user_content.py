from __future__ import annotations

from pydantic import TypeAdapter
import pytest

from vibe.app_server.models import (
    ContentBlock,
    PublicMessageEntry,
    ResourceContentBlock,
    validate_history_entry,
)
from vibe.user_content import UserBlobResource, UserTextResource


def test_kindless_text_resource_loads_as_text() -> None:
    """A resource stored without a ``kind`` tag infers ``text`` from its keys.

    Sessions written by code that serializes the harness interop shape carry
    embedded resources as ``{"uri", "text"}``; they must stay resumable.
    """
    block = ResourceContentBlock.model_validate({
        "type": "resource",
        "resource": {"uri": "file:///tmp/lol.ts", "text": "hi"},
    })

    assert isinstance(block.resource, UserTextResource)
    assert block.resource.uri == "file:///tmp/lol.ts"
    assert block.resource.text == "hi"


def test_kindless_blob_resource_loads_as_blob() -> None:
    block = ResourceContentBlock.model_validate({
        "type": "resource",
        "resource": {"uri": "file:///tmp/lol.bin", "blob": "aGk="},
    })

    assert isinstance(block.resource, UserBlobResource)


def test_kindless_resource_validates_inside_message_content() -> None:
    """The message-content union is the path a resumed transcript takes."""
    blocks = TypeAdapter(list[ContentBlock]).validate_python([
        {"type": "text", "text": "read this"},
        {"type": "resource", "resource": {"uri": "file:///tmp/lol.ts", "text": "hi"}},
    ])

    assert [type(block) for block in blocks] == [type(blocks[0]), ResourceContentBlock]


def test_explicit_kind_still_selects_the_variant() -> None:
    block = ResourceContentBlock.model_validate({
        "type": "resource",
        "resource": {"kind": "text", "uri": "f", "text": "hi"},
    })

    assert isinstance(block.resource, UserTextResource)


def test_resource_with_neither_text_nor_blob_is_still_rejected() -> None:
    """The inference is a legacy migration, not a blanket tolerance."""
    with pytest.raises(Exception, match="discriminator 'kind'"):
        ResourceContentBlock.model_validate({
            "type": "resource",
            "resource": {"uri": "file:///tmp/lol.ts"},
        })


def test_kindless_resource_round_trip_writes_the_tag() -> None:
    """The public model serializes the inferred tag; the harness-owned store
    keeps its untagged format, so the inference is a permanent translation.
    """
    block = ResourceContentBlock.model_validate({
        "type": "resource",
        "resource": {"uri": "file:///tmp/lol.ts", "text": "hi"},
    })

    assert block.model_dump(by_alias=True)["resource"]["kind"] == "text"


def _stored_history_entry() -> dict:
    """A resumed transcript entry in the harness storage shape: the resource
    carries ``mimeType`` (rmcp's spelling) and no ``kind`` tag.
    """
    return {
        "type": "message",
        "id": "m1",
        "sessionId": "s1",
        "turnId": None,
        "createdAt": 0,
        "updatedAt": 0,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "role": "user",
        "content": [
            {"type": "text", "text": "read this"},
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///tmp/lol.ts",
                    "mimeType": "text/x-typescript",
                    "text": "hi",
                },
            },
        ],
        "source": "turn_start",
    }


def test_stored_message_with_mime_type_validates_through_history_entry() -> None:
    """The exact reported resume path: a stored entry whose resource carries
    ``mimeType`` must survive ``validate_history_entry``.
    """
    entry = validate_history_entry(_stored_history_entry())

    assert isinstance(entry, PublicMessageEntry)
    block = entry.content[1]
    assert isinstance(block, ResourceContentBlock)
    resource = block.resource
    assert isinstance(resource, UserTextResource)
    assert resource.media_type == "text/x-typescript"


def test_resource_media_type_accepts_both_spellings_and_writes_the_public_one() -> None:
    block = ResourceContentBlock.model_validate({
        "type": "resource",
        "resource": {"uri": "f", "mimeType": "text/x-typescript", "text": "hi"},
    })

    assert block.resource.media_type == "text/x-typescript"
    assert (
        block.model_dump(by_alias=True)["resource"]["mediaType"] == "text/x-typescript"
    )

    block = ResourceContentBlock.model_validate({
        "type": "resource",
        "resource": {"uri": "f", "mediaType": "text/x-typescript", "text": "hi"},
    })

    assert block.resource.media_type == "text/x-typescript"
