from __future__ import annotations

import base64
import re

import pytest

from vibe.core.skills.registry.models import (
    ListSkillsResponse,
    RegistryAssetContent,
    RegistrySkillItem,
    sanitize_skill_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("my_skill", "my-skill"),
        ("My Skill", "my-skill"),
        ("  weird__name--ok  ", "weird-name-ok"),
        ("grill-me", "grill-me"),
        ("___", None),
        ("", None),
    ],
)
def test_sanitize_skill_name(raw: str, expected: str | None) -> None:
    assert sanitize_skill_name(raw) == expected


def test_asset_to_bytes_text() -> None:
    asset = RegistryAssetContent(text_content="hello")
    assert asset.to_bytes() == b"hello"


def test_asset_to_bytes_raw_base64() -> None:
    encoded = base64.b64encode(b"\x00\x01binary").decode("ascii")
    asset = RegistryAssetContent(raw_content=encoded, is_executable=True)
    assert asset.to_bytes() == b"\x00\x01binary"
    assert asset.is_executable is True


def test_asset_to_bytes_invalid_base64_returns_none() -> None:
    assert RegistryAssetContent(raw_content="not!base64!").to_bytes() is None


def test_asset_to_bytes_empty_returns_none() -> None:
    assert RegistryAssetContent().to_bytes() is None


def test_item_parses_camel_case() -> None:
    item = RegistrySkillItem.model_validate({
        "skillId": "abc",
        "skill": {
            "skillName": "Free Form",
            "skillDescription": "does things",
            "skillBody": "# body",
            "skillAssets": {"ref.txt": {"textContent": "x", "isExecutable": False}},
        },
        "metadata": {"name": "my_skill", "latestVersion": 3},
        "version": 3,
    })
    assert item.skill_id == "abc"
    assert item.resolved_name == "my-skill"
    assert item.resolved_description == "does things"
    assert item.skill.skill_assets["ref.txt"].text_content == "x"
    assert item.version == 3


def test_item_parses_snake_case_fallback() -> None:
    item = RegistrySkillItem.model_validate({
        "skill_id": "abc",
        "skill": {"skill_name": "n", "skill_body": "b"},
        "metadata": {"name": "snake_name"},
    })
    assert item.resolved_name == "snake-name"


def test_item_name_falls_back_to_skill_name() -> None:
    item = RegistrySkillItem.model_validate({
        "skill": {"skillName": "Fallback Name", "skillBody": "b"}
    })
    assert item.resolved_name == "fallback-name"


def test_item_name_falls_back_to_title() -> None:
    item = RegistrySkillItem.model_validate({
        "skillId": "abc",
        "skill": {"skillBody": "b"},
        "attributes": {"title": "My Cool Skill"},
    })
    assert item.resolved_name == "my-cool-skill"


def test_item_name_falls_back_to_skill_id() -> None:
    item = RegistrySkillItem.model_validate({
        "skillId": "019de91a-84f5-76af-84a2-5f4389f372c7",
        "skill": {"skillBody": "b"},
    })
    assert item.resolved_name == "skill-019de91a84f576af84a25f4389f372c7"


def test_item_name_none_without_any_identifier() -> None:
    item = RegistrySkillItem.model_validate({"skill": {"skillBody": "b"}})
    assert item.resolved_name is None


def test_item_description_falls_back_to_attributes() -> None:
    item = RegistrySkillItem.model_validate({
        "skill": {"skillName": "n", "skillBody": "b"},
        "attributes": {"title": "T", "description": "attr desc"},
    })
    assert item.resolved_description == "attr desc"


def test_list_response_parses_pagination() -> None:
    response = ListSkillsResponse.model_validate({
        "data": [{"skillId": "1", "skill": {"skillName": "a", "skillBody": "b"}}],
        "nextPageToken": "next",
    })
    assert len(response.data) == 1
    assert response.next_page_token == "next"


def test_list_response_defaults_empty() -> None:
    response = ListSkillsResponse.model_validate({})
    assert response.data == []
    assert response.next_page_token == ""


# Resolved/sanitized names must always satisfy SkillMetadata.name's pattern.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_sanitize_skill_name_caps_at_64_chars() -> None:
    name = sanitize_skill_name("x" * 200)
    assert name is not None
    assert len(name) <= 64
    assert _NAME_RE.match(name)


def test_resolved_name_id_fallback_is_lowercase_and_valid() -> None:
    # No usable name anywhere -> falls back to the skill id, which may be
    # uppercase; it must still be a valid, lowercase skill name.
    item = RegistrySkillItem.model_validate({
        "skillId": "AB12CD34-EF56-7890-ABCD-EF1234567890"
    })
    assert item.resolved_name == "skill-ab12cd34ef567890abcdef1234567890"
    assert _NAME_RE.match(item.resolved_name or "")


def test_resolved_name_id_fallback_uses_full_id_to_avoid_collisions() -> None:
    # Ids sharing a prefix (common for time-ordered UUIDv7) must not collapse
    # to the same fallback name.
    a = RegistrySkillItem.model_validate({
        "skillId": "ab12cd34-0000-0000-0000-000000000001"
    })
    b = RegistrySkillItem.model_validate({
        "skillId": "ab12cd34-0000-0000-0000-000000000002"
    })
    assert a.resolved_name != b.resolved_name


def test_resolved_name_id_fallback_handles_degenerate_id() -> None:
    # A hyphen-only id must collapse to a valid name, not a trailing-hyphen
    # "skill-" that would fail validation.
    item = RegistrySkillItem.model_validate({"skillId": "----"})
    assert item.resolved_name == "skill"
    assert _NAME_RE.match(item.resolved_name or "")
