from __future__ import annotations

from vibe.user_content import UserDisplayContent

USER_DISPLAY_CONTENT_META_KEY = "user_display_content"


def parse_user_display_content_metadata(value: object) -> UserDisplayContent | None:
    if value is None:
        return None

    return UserDisplayContent.model_validate(value)
