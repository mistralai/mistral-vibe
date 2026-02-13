from __future__ import annotations

import logging
from xml.sax.saxutils import escape

from vibe.core.memory.models import Seed, UserField
from vibe.core.memory.storage import MemoryStore

logger = logging.getLogger(__name__)

# Approximate: 1 token ≈ 4 characters
_CHARS_PER_TOKEN = 4


class MemoryInjector:
    """Builds the ephemeral <memory> XML block for injection into backend calls."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def build_memory_block(
        self, user_id: str, context_key: str, budget_tokens: int = 500
    ) -> str:
        state = self._store.get_or_create_user_state(user_id)
        ctx_mem = self._store.get_or_create_context_memory(context_key, user_id)

        # Build sections by priority (highest first — trimmed from bottom)
        seed_section = escape(self._format_seed(state.seed))
        fields_section = escape(self._format_fields(state.fields))
        active_section = escape(self._format_short_term(ctx_mem.short_term))
        knowledge_section = escape(ctx_mem.long_term.strip())
        sensory_section = escape(self._format_sensory(ctx_mem.sensory))

        # Nothing to inject
        if not any([seed_section, fields_section, active_section, knowledge_section, sensory_section]):
            return ""

        budget_chars = budget_tokens * _CHARS_PER_TOKEN

        # Assemble with priority-based truncation (flat structure)
        parts: list[str] = []
        remaining = budget_chars

        remaining = self._append_section(parts, "seed", seed_section, remaining)
        remaining = self._append_section(parts, "fields", fields_section, remaining)
        remaining = self._append_section(parts, "active", active_section, remaining)
        remaining = self._append_section(
            parts, "knowledge", knowledge_section, remaining
        )
        remaining = self._append_section(parts, "recent", sensory_section, remaining)

        if not parts:
            return ""

        return "<memory>\n" + "\n".join(parts) + "\n</memory>"

    @staticmethod
    def _append_section(
        parts: list[str], tag: str, section: str, remaining: int
    ) -> int:
        if not section:
            return remaining

        overhead = (len(tag) * 2) + 5  # "<tag>" + "</tag>"
        if remaining <= overhead:
            return remaining

        trimmed = section[: remaining - overhead]
        if not trimmed:
            return remaining

        parts.append(f"<{tag}>{trimmed}</{tag}>")
        return remaining - (len(trimmed) + overhead)

    @staticmethod
    def _format_seed(seed: Seed) -> str:
        return seed.format_summary()

    @staticmethod
    def _format_fields(fields: list[UserField]) -> str:
        if not fields:
            return ""
        # Sort by access_count descending for priority
        sorted_fields = sorted(
            fields, key=lambda f: f.meta.access_count, reverse=True
        )
        return " | ".join(f"{f.key}: {f.value}" for f in sorted_fields)

    @staticmethod
    def _format_short_term(items: list[str]) -> str:
        if not items:
            return ""
        return "\n".join(f"- {item}" for item in items)

    @staticmethod
    def _format_sensory(items: list[str], limit: int = 10) -> str:
        if not items:
            return ""
        recent = items[-limit:]
        return "\n".join(f"- {item}" for item in recent)
