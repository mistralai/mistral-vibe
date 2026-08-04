from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from vibe.core.types import FileImageSource, ImageAttachment, InlineImageSource


class ImageReadError(Exception):
    pass


_CACHE_MAX = 32


@lru_cache(maxsize=_CACHE_MAX)
def _encode_cached(path_str: str, mtime_ns: int, size: int) -> str:
    try:
        return base64.b64encode(Path(path_str).read_bytes()).decode("ascii")
    except OSError as e:
        raise ImageReadError(f"Failed to read image {path_str}: {e}") from e


def _encode(att: ImageAttachment) -> str:
    match att.source:
        case InlineImageSource(data=data):
            return data
        case FileImageSource(path=path):
            try:
                stat = path.stat()
            except OSError as e:
                raise ImageReadError(f"Failed to stat image {path}: {e}") from e
            return _encode_cached(str(path), stat.st_mtime_ns, stat.st_size)


def to_data_uri(att: ImageAttachment) -> str:
    return f"data:{att.mime_type};base64,{_encode(att)}"


def to_base64(att: ImageAttachment) -> str:
    return _encode(att)
