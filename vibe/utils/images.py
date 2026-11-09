from __future__ import annotations

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 8

__all__ = ["IMAGE_EXTENSIONS", "MAX_IMAGES_PER_MESSAGE", "MAX_IMAGE_BYTES"]
