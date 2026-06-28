from __future__ import annotations

from vibe.core.types import UserDisplayContentMetadata

USER_DISPLAY_CONTENT_META_KEY = "user_display_content"


def parse_user_display_content_metadata(
    value: object,
) -> UserDisplayContentMetadata | None:
    if value is None:
        return None

    return UserDisplayContentMetadata.model_validate(value)
