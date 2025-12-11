from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:
    try:
        from strenum import StrEnum
    except ImportError:
        # Fallback if strenum not installed
        from enum import Enum

        class StrEnum(str, Enum):
            pass
