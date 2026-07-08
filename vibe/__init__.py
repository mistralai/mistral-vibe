from __future__ import annotations

import os
from pathlib import Path

# Strip the "For further information visit https://errors.pydantic.dev/..." line
# from ValidationError string output. pydantic-core caches this setting on the
# first error render, so it must be set before any validation error is stringified.
os.environ.setdefault("PYDANTIC_ERRORS_INCLUDE_URL", "0")

VIBE_ROOT = Path(__file__).parent
__version__ = "2.19.1"
