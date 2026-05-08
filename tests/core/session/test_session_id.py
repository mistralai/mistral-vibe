from __future__ import annotations

import re

import pytest

from vibe.core.session.session_id import extract_suffix, generate_session_id

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class TestGenerateSessionId:
    def test_uuid_shape(self) -> None:
        sid = generate_session_id()
        assert UUID_RE.match(sid), f"Not UUID-shaped: {sid}"
        assert len(sid) == 36

    def test_unique_ids(self) -> None:
        ids = {generate_session_id() for _ in range(100)}
        assert len(ids) == 100

    def test_suffix_preserved(self) -> None:
        suffix = "aabbccddeeff"
        sid = generate_session_id(suffix=suffix)
        assert UUID_RE.match(sid)
        assert sid.endswith(suffix)

    def test_suffix_stable_across_regeneration(self) -> None:
        suffix = "112233445566"
        ids = [generate_session_id(suffix=suffix) for _ in range(50)]
        # All share the same last segment
        assert all(s.rsplit("-", 1)[-1] == suffix for s in ids)
        # But first 8 chars are different (probabilistically)
        prefixes = {s[:8] for s in ids}
        assert len(prefixes) > 1

    def test_default_suffix_is_12_hex(self) -> None:
        sid = generate_session_id()
        suffix = sid.rsplit("-", 1)[-1]
        assert len(suffix) == 12
        assert re.fullmatch(r"[0-9a-f]+", suffix)

    def test_first_8_chars_are_unique_with_same_suffix(self) -> None:
        suffix = "abcdef123456"
        a = generate_session_id(suffix=suffix)
        b = generate_session_id(suffix=suffix)
        assert a[:8] != b[:8]


class TestExtractSuffix:
    def test_extract_from_generated_id(self) -> None:
        sid = generate_session_id()
        suffix = extract_suffix(sid)
        assert sid.endswith(suffix)
        assert len(suffix) == 12

    def test_extract_from_real_uuid(self) -> None:
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        assert extract_suffix(uuid_str) == "446655440000"

    def test_extract_roundtrip(self) -> None:
        original_suffix = "deadbeef1234"
        sid = generate_session_id(suffix=original_suffix)
        assert extract_suffix(sid) == original_suffix

    def test_extract_from_no_hyphen(self) -> None:
        assert extract_suffix("abcdef") == "abcdef"

    @pytest.mark.parametrize(
        "session_id",
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "abcdef01-2345-6789-abcd-ef0123456789",
            "12345678-aaaa-bbbb-cccc-aabbccddeeff",
        ],
    )
    def test_extract_always_returns_last_segment(self, session_id: str) -> None:
        expected = session_id.rsplit("-", 1)[-1]
        assert extract_suffix(session_id) == expected
