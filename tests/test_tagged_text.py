from __future__ import annotations

import pytest

from vibe.core.utils import CANCELLATION_TAG, KNOWN_TAGS, TaggedText


def test_tagged_text_creation_without_tag() -> None:
    tagged = TaggedText("Hello world")
    assert tagged.message == "Hello world"
    assert tagged.tag == ""
    assert str(tagged) == "Hello world"


def test_tagged_text_creation_with_tag() -> None:
    tagged = TaggedText("User cancelled", CANCELLATION_TAG)
    assert tagged.message == "User cancelled"
    assert tagged.tag == CANCELLATION_TAG
    assert str(tagged) == f"<{CANCELLATION_TAG}>User cancelled</{CANCELLATION_TAG}>"


@pytest.mark.parametrize("tag", KNOWN_TAGS)
def test_tagged_text_from_string_with_known_tag(tag: str) -> None:
    text = f"<{tag}>This is a tagged text</{tag}>"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "This is a tagged text"
    assert tagged.tag == tag


@pytest.mark.parametrize("tag", KNOWN_TAGS)
def test_tagged_text_from_string_with_known_tag_multiline(tag: str) -> None:
    text = f"<{tag}>This is a tagged text</{tag}>"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "This is a tagged text"
    assert tagged.tag == tag


@pytest.mark.parametrize("tag", KNOWN_TAGS)
def test_tagged_text_from_string_with_known_tag_whitespace(tag: str) -> None:
    text = f"<{tag}>  This is a tagged text  </{tag}>"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "  This is a tagged text  "
    assert tagged.tag == tag


def test_tagged_text_from_string_with_unknown_tag() -> None:
    text = "<unknown_tag>Some content</unknown_tag>"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "<unknown_tag>Some content</unknown_tag>"
    assert tagged.tag == ""


def test_tagged_text_from_string_with_text_before_tag() -> None:
    text = f"Prefix text <{CANCELLATION_TAG}>Content</{CANCELLATION_TAG}>"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "Prefix text Content"
    assert tagged.tag == CANCELLATION_TAG


def test_tagged_text_from_string_with_text_after_tag() -> None:
    text = f"<{CANCELLATION_TAG}>Content</{CANCELLATION_TAG}> Suffix text"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "Content Suffix text"
    assert tagged.tag == CANCELLATION_TAG


def test_tagged_text_from_string_with_text_before_and_after_tag() -> None:
    text = f"Before <{CANCELLATION_TAG}>Content</{CANCELLATION_TAG}> After"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "Before Content After"
    assert tagged.tag == CANCELLATION_TAG


def test_tagged_text_from_string_without_tags() -> None:
    text = "Just plain text without any tags"
    tagged = TaggedText.from_string(text)
    assert tagged.message == "Just plain text without any tags"
    assert tagged.tag == ""


def test_tagged_text_from_string_empty() -> None:
    tagged = TaggedText.from_string("")
    assert tagged.message == ""
    assert tagged.tag == ""


def test_tagged_text_from_string_mismatched_tags() -> None:
    text = f"<{CANCELLATION_TAG}>Content</different_tag>"
    tagged = TaggedText.from_string(text)
    assert tagged.message == f"<{CANCELLATION_TAG}>Content</different_tag>"
    assert tagged.tag == ""


def test_tagged_text_round_trip() -> None:
    original = TaggedText("User cancelled", CANCELLATION_TAG)
    text = str(original)
    parsed = TaggedText.from_string(text)
    assert parsed.message == original.message
    assert parsed.tag == original.tag


def test_tagged_text_round_trip_no_tag() -> None:
    original = TaggedText("Plain message")
    text = str(original)
    parsed = TaggedText.from_string(text)
    assert parsed.message == original.message
    assert parsed.tag == original.tag
