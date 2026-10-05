"""The URL shape Vibe displays and logs.

Several surfaces rebuild a URL to drop the parts that carry credentials or
noise (userinfo, query, fragment). Duplicated rebuilds drift — the first
duplicate already disagreed about IPv6 bracketing — so new display code uses
this definition instead of growing another one.
"""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit, urlunsplit

__all__ = ["display_url", "display_url_or_raw", "strip_url_credentials"]


def _netloc(parts: SplitResult) -> str:
    # ``hostname`` is lowercased and unbracketed, so an IPv6 host must be
    # re-bracketed before a port is appended.
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return netloc


def display_url(parts: SplitResult) -> str:
    """The URL rebuilt as ``scheme://host[:port]path``.

    Userinfo, query, and fragment are dropped. Raises ``ValueError`` when the
    netloc cannot be parsed (an invalid port).
    """
    return urlunsplit((parts.scheme, _netloc(parts), parts.path, "", ""))


def strip_url_credentials(value: str) -> str | None:
    """Drop userinfo, query, and fragment from a URL-shaped value.

    A scheme-less misconfiguration (``user:pass@host``, ``host/path?token``)
    is parsed as protocol-relative so its authority is recognized. The
    netloc is split textually rather than rebuilt from ``hostname`` and
    ``port`` so an invalid port does not hide the rest of the value. A
    value whose authority cannot be parsed at all is not safely displayable.
    """
    schemeless = "://" not in value and not value.startswith("//")
    try:
        parts = urlsplit(f"//{value}" if schemeless else value)
    except ValueError:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1] if "@" in parts.netloc else parts.netloc
    stripped = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    if schemeless and netloc:
        # ``urlunsplit`` emitted the protocol-relative ``//`` the re-parse
        # added; the configured value had no scheme to show.
        stripped = stripped[2:]
    return stripped


def display_url_or_raw(url: str) -> str:
    """``display_url`` for untrusted text.

    A URL whose netloc cannot be rebuilt is stripped textually rather than
    returned verbatim: some callers (the plugin catalog) publish the result
    without a value-based redaction pass, so the parts that carry
    credentials must never survive. A value that cannot even be split has
    nothing left to show and publishes as an empty string.
    """
    try:
        return display_url(urlsplit(url))
    except ValueError:
        return strip_url_credentials(url) or ""
