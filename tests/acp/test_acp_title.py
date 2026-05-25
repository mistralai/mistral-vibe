from __future__ import annotations

from acp.schema import (
    EmbeddedResourceContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)

from vibe.acp.title import acp_blocks_to_title_segments
from vibe.core.session.title_format import MentionSegment, TextSegment


class TestAcpBlocksToTitleSegments:
    def test_text_block(self) -> None:
        segments = acp_blocks_to_title_segments([
            TextContentBlock(type="text", text="hello")
        ])
        assert segments == [TextSegment(text="hello")]

    def test_resource_link_with_name(self) -> None:
        block = ResourceContentBlock(
            type="resource_link", uri="file:///abs/path/foo.py", name="foo.py"
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="foo.py")]

    def test_resource_link_with_line_range_fragment(self) -> None:
        block = ResourceContentBlock(
            type="resource_link", uri="file:///abs/path/foo.py#L9-L27", name="foo.py"
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="foo.py", start_line=9, end_line=27)]

    def test_resource_link_with_single_line_fragment(self) -> None:
        block = ResourceContentBlock(
            type="resource_link", uri="file:///abs/path/foo.py#L42", name="foo.py"
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="foo.py", start_line=42, end_line=None)]

    def test_resource_link_falls_back_to_uri_basename_when_no_name(self) -> None:
        block = ResourceContentBlock(
            type="resource_link", uri="file:///abs/path/bar.txt", name=""
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="bar.txt")]

    def test_embedded_resource_block_derives_basename_from_uri(self) -> None:
        block = EmbeddedResourceContentBlock(
            type="resource",
            resource=TextResourceContents(
                uri="file:///abs/path/script.sh", text="echo hi"
            ),
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="script.sh")]

    def test_embedded_resource_with_line_range_fragment(self) -> None:
        block = EmbeddedResourceContentBlock(
            type="resource",
            resource=TextResourceContents(
                uri="file:///abs/path/main.py#L1-L20", text="content"
            ),
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="main.py", start_line=1, end_line=20)]

    def test_automatic_field_meta_skips_block(self) -> None:
        block = EmbeddedResourceContentBlock(
            type="resource",
            field_meta={"automatic": True},
            resource=TextResourceContents(uri="file:///abs/path/auto.py", text="..."),
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == []

    def test_automatic_field_meta_skips_resource_link(self) -> None:
        block = ResourceContentBlock(
            type="resource_link",
            uri="file:///abs/path/auto.py",
            name="auto.py",
            field_meta={"automatic": True},
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == []

    def test_preserves_order(self) -> None:
        blocks = [
            TextContentBlock(type="text", text="Look at "),
            ResourceContentBlock(
                type="resource_link", uri="file:///a/foo.py", name="foo.py"
            ),
            TextContentBlock(type="text", text=" and "),
            ResourceContentBlock(
                type="resource_link", uri="file:///a/bar.py", name="bar.py"
            ),
        ]
        segments = acp_blocks_to_title_segments(blocks)
        assert segments == [
            TextSegment(text="Look at "),
            MentionSegment(name="foo.py"),
            TextSegment(text=" and "),
            MentionSegment(name="bar.py"),
        ]

    def test_unknown_fragment_format_ignored(self) -> None:
        block = ResourceContentBlock(
            type="resource_link", uri="file:///abs/path/foo.py#section1", name="foo.py"
        )
        segments = acp_blocks_to_title_segments([block])
        assert segments == [MentionSegment(name="foo.py")]
