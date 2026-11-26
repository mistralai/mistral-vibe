from __future__ import annotations

from collections.abc import Callable

type RequestMetadataGetter = Callable[[], dict[str, str]]
