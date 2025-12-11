from __future__ import annotations

import logging
from typing import Final

try:
    import tiktoken
except ImportError:
    tiktoken = None

logger = logging.getLogger(__name__)

# Heuristic constant: average characters per token
CHARS_PER_TOKEN: Final[float] = 4.0


def count_tokens(text: str, model_name: str = "gpt-3.5-turbo") -> int:
    """Count tokens in a text string using tiktoken if available, or a heuristic.

    Args:
        text: The text to count tokens for.
        model_name: The model name to use for encoding lookups.

    Returns:
        Integer count of tokens.
    """
    if not text:
        return 0

    if tiktoken:
        try:
            try:
                encoding = tiktoken.encoding_for_model(model_name)
            except KeyError:
                # Default to cl100k_base for modern models if unknown
                encoding = tiktoken.get_encoding("cl100k_base")

            return len(encoding.encode(text))
        except Exception:
            logger.warning("Failed to count tokens with tiktoken, falling back to heuristic")
            pass

    # Fallback heuristic
    return int(len(text) / CHARS_PER_TOKEN)


def estimate_char_count(tokens: int) -> int:
    """Estimate character count from token count."""
    return int(tokens * CHARS_PER_TOKEN)
