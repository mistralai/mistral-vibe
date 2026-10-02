from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.vibe._storage import canonical_json, sha256_json

UNSAFE_INTEGER = -6917529027642130433


def test_safe_payload_digest_is_unchanged() -> None:
    payload = {"b": 1, "a": [True, None, "x"]}

    assert canonical_json(payload) == b'{"a":[true,null,"x"],"b":1}'
    assert sha256_json(payload) == hashlib.sha256(canonical_json(payload)).hexdigest()


def test_integers_beyond_the_safe_range_do_not_raise() -> None:
    payload = {"sortOrder": UNSAFE_INTEGER, "nested": [{"id": 2**60}], "ok": 7}

    assert canonical_json(payload) == (
        b'{"nested":[{"id":"1152921504606846976"}],"ok":7,'
        b'"sortOrder":"-6917529027642130433"}'
    )
    assert sha256_json(payload) == sha256_json(payload)


def test_safe_boundary_integers_stay_numbers() -> None:
    largest_safe = 2**53 - 1

    assert canonical_json({"n": largest_safe}) == b'{"n":9007199254740991}'
    assert canonical_json({"n": -largest_safe}) == b'{"n":-9007199254740991}'
    assert canonical_json({"n": largest_safe + 1}) == b'{"n":"9007199254740992"}'
