from __future__ import annotations

import pytest

from vibe.core.utils import get_server_url_from_api_base


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        ("https://api.mistral.ai/v1", "https://api.mistral.ai"),
        ("https://on-prem.example.com/v1", "https://on-prem.example.com"),
        ("http://localhost:8080/v2", "http://localhost:8080"),
        ("not-a-url", None),
        ("ftp://example.com/v1", None),
    ],
)
def test_get_server_url_from_api_base(api_base, expected):
    assert get_server_url_from_api_base(api_base) == expected
