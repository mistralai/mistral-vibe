from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from vibe.core.memory._parsing import strip_markdown_fences
from vibe.core.memory.config import MemoryConfig
from vibe.core.memory.decay import apply_decay, reinforce_field
from vibe.core.memory.models import Observation, Seed, UserField, UserState
from vibe.core.memory.prompts import MemoryPrompt
from vibe.core.memory.storage import MemoryStore

logger = logging.getLogger(__name__)


class ReflectionEngine:
    """Synthesizes observations into user state updates.

    When accumulated_importance >= reflection_trigger, fetches top observations,
    asks the LLM to produce seed/field updates, applies them to user state,
    enforces the field cap via Ebbinghaus decay, and resets the accumulator.
    """

    def __init__(
        self,
        llm_caller: Callable[[str, str], Awaitable[str]],
        store: MemoryStore,
    ) -> None:
        self._llm_caller = llm_caller
        self._store = store

    async def maybe_reflect(self, user_id: str, config: MemoryConfig) -> bool:
        """Check if reflection is needed and perform it. Returns True if reflection occurred."""
        state = self._store.get_or_create_user_state(user_id)
        if state.accumulated_importance < config.reflection_trigger:
            return False

        observations = self._store.get_pending_observations(user_id, limit=20)
        if not observations:
            return False

        try:
            result = await self._call_llm(state, observations, config)
        except Exception:
            logger.warning("Reflection LLM call failed", exc_info=True)
            return False

        try:
            self._apply_result(user_id, state, result, observations, config)
        except Exception:
            logger.warning("Reflection apply failed", exc_info=True)
            return False

        return True

    async def _call_llm(
        self, state: UserState, observations: list[Observation], config: MemoryConfig
    ) -> dict:
        prompt_template = MemoryPrompt.REFLECT.read()

        current_state_str = self._format_state(state)
        obs_str = "\n".join(
            f"- [{o.importance}] ({o.source_role}) {o.content}" for o in observations
        )

        system_prompt = prompt_template.format(
            current_state=current_state_str,
            observations=obs_str,
        )

        raw = await self._llm_caller(system_prompt, "Reflect on the observations and produce the JSON update.")
        return self._parse_response(raw)

    def _apply_result(
        self,
        user_id: str,
        state: UserState,
        result: dict,
        observations: list[Observation],
        config: MemoryConfig,
    ) -> None:
        now = datetime.now(UTC)

        # Apply seed updates
        seed_updates = result.get("seed_updates", {})
        if seed_updates:
            current = state.seed.model_dump()
            for k, v in seed_updates.items():
                if not v:
                    continue
                # LLM may return dicts/lists instead of strings; coerce to str.
                if isinstance(v, str):
                    current[k] = v
                else:
                    current[k] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            state.seed = Seed(**current)

        # Apply field updates
        field_updates = result.get("field_updates", [])
        fields_by_key = {f.key: f for f in state.fields}

        for update in field_updates:
            action = update.get("action", "")
            key = update.get("key", "")
            value = update.get("value", "")

            if not key:
                continue

            if action == "add":
                if key not in fields_by_key:
                    fields_by_key[key] = UserField(key=key, value=value)
                else:
                    # Reinforce existing field if "add" targets an existing key
                    fields_by_key[key] = reinforce_field(
                        UserField(key=key, value=value, meta=fields_by_key[key].meta), now
                    )
            elif action == "update":
                if key in fields_by_key:
                    fields_by_key[key] = reinforce_field(
                        UserField(key=key, value=value, meta=fields_by_key[key].meta), now
                    )
                else:
                    fields_by_key[key] = UserField(key=key, value=value)
            elif action == "remove":
                fields_by_key.pop(key, None)

        state.fields = list(fields_by_key.values())

        # Proactive decay: always prune stale fields, not just on cap exceeded
        if config.decay_enabled:
            state.fields = apply_decay(
                state.fields, now, prune_threshold=config.decay_prune_threshold
            )
            if len(state.fields) > config.max_user_fields:
                state.fields.sort(
                    key=lambda f: (f.meta.strength, f.meta.last_accessed),
                    reverse=True,
                )
                state.fields = state.fields[: config.max_user_fields]

        # Reset accumulator
        state.accumulated_importance = 0.0
        self._store.update_user_state(state)

        # Audit log (before clearing observations)
        obs_summary = json.dumps(
            [{"content": o.content, "importance": o.importance} for o in observations]
        )
        self._store.log_reflection(user_id, obs_summary, json.dumps(result))

        # Clear only processed observations so unprocessed backlog is preserved.
        self._store.clear_observations(user_id, processed=observations)

    @staticmethod
    def _format_state(state: UserState) -> str:
        parts = []
        seed_summary = state.seed.format_summary()
        if seed_summary:
            parts.append("Seed: " + seed_summary)

        if state.fields:
            # Include strength so the LLM can see which fields are reinforced
            # vs weakly held (strength 1.0 = mentioned once, higher = reinforced).
            field_strs = [
                f"{f.key}: {f.value} (strength={f.meta.strength:.1f})"
                for f in state.fields
            ]
            parts.append("Fields:\n" + "\n".join(f"  - {s}" for s in field_strs))

        return "\n".join(parts) if parts else "(empty state)"

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """Extract JSON from the LLM response, handling markdown fences."""
        text = strip_markdown_fences(raw)
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return {}
            return parsed
        except json.JSONDecodeError:
            logger.warning("Failed to parse reflection JSON: %s", text[:200])
            return {}
