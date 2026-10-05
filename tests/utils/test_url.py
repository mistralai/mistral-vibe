"""The canonical URL rebuild: locator kept, credentials and noise dropped."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from vibe.utils.url import display_url, display_url_or_raw


def test_keeps_scheme_host_port_and_path() -> None:
    assert display_url(urlsplit("https://api.example.com:8443/v1")) == (
        "https://api.example.com:8443/v1"
    )


def test_drops_userinfo_query_and_fragment() -> None:
    assert display_url(urlsplit("https://user:pass@api.example.com/v1?k=v#f")) == (
        "https://api.example.com/v1"
    )


def test_ipv6_host_is_bracketed_with_and_without_port() -> None:
    assert display_url(urlsplit("https://[2001:db8::1]:8443/v1")) == (
        "https://[2001:db8::1]:8443/v1"
    )
    assert (
        display_url(urlsplit("https://[2001:db8::1]/v1")) == "https://[2001:db8::1]/v1"
    )


def test_invalid_port_raises() -> None:
    with pytest.raises(ValueError):
        display_url(urlsplit("https://api.example.com:not-a-port/v1"))


def test_or_raw_matches_display_url_for_parseable_input() -> None:
    assert display_url_or_raw("https://api.example.com:8443/v1?k=v") == (
        "https://api.example.com:8443/v1"
    )


def test_or_raw_keeps_an_unparsable_locator_without_its_credentials() -> None:
    """The plugin catalog publishes this result without a value-based pass,
    so an unparsable netloc must not publish the parts it carries.
    """
    assert display_url_or_raw("https://api.example.com:not-a-port/v1?k=v") == (
        "https://api.example.com:not-a-port/v1"
    )
    assert display_url_or_raw("https://user:pw@api.example.com:not-a-port/v1") == (
        "https://api.example.com:not-a-port/v1"
    )


def test_or_raw_publishes_nothing_it_cannot_split() -> None:
    assert display_url_or_raw("https://[2001:db8::1/v1?k=v") == ""
