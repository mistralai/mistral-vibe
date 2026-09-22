"""Platform-aware path conversion for Vibe Runtime adapters."""

from nturl2path import url2pathname as windows_url2pathname
import sys
from urllib.parse import unquote, urlparse


def is_windows() -> bool:
    return sys.platform == "win32"


def file_uri_to_path(uri: str) -> str:
    """Convert a file URI into a path using the current platform's rules."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Expected a file URI: {uri!r}")

    encoded_path = parsed.path
    if parsed.netloc and parsed.netloc != "localhost":
        encoded_path = f"//{parsed.netloc}{encoded_path}"
    if is_windows():
        return windows_url2pathname(encoded_path)
    return unquote(encoded_path)


__all__ = ["file_uri_to_path"]
