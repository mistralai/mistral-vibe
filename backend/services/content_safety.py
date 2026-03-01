"""Content safety / moderation screening.

Uses ``mistral-moderation-2411`` to screen user-submitted text before
it reaches other AI services, as described in PRD §5.4.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.config import settings
from backend.models.moderation import ContentSafetyResult
from backend.services.mistral_client import get_client

logger = logging.getLogger(__name__)


async def screen_content(text: str) -> ContentSafetyResult:
    """Screen user text for harmful content.

    Returns a ``ContentSafetyResult`` indicating whether the input is
    safe and listing any flagged categories.
    """
    client = get_client()
    if client is None:
        return ContentSafetyResult(is_safe=True, explanation="Mock mode — content not screened.")

    try:
        response = client.classifiers.moderate_chat(
            model=settings.mistral_moderation_model,
            inputs=[{"role": "user", "content": text}],
        )

        flagged_categories: list[str] = []
        is_safe = True
        for result in response.results:
            for category, flagged in result.categories.items():
                if flagged:
                    flagged_categories.append(category)
                    is_safe = False

        return ContentSafetyResult(
            is_safe=is_safe,
            categories=flagged_categories,
            explanation="Content flagged for: " + ", ".join(flagged_categories) if flagged_categories else "Content is safe.",
        )
    except Exception:
        logger.exception("Content moderation failed")
        return ContentSafetyResult(is_safe=True, explanation="Moderation check failed — allowing content.")
