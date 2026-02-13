from __future__ import annotations

import math
from datetime import UTC, datetime

from vibe.core.memory.models import FieldMeta, UserField


def compute_retention(meta: FieldMeta, now: datetime) -> float:
    """Ebbinghaus retention: R = e^(-t/S) where t = days since last access, S = strength."""
    if meta.strength <= 0:
        return 0.0
    t = (now - meta.last_accessed).total_seconds() / 86400.0
    if t <= 0:
        return 1.0
    return math.exp(-t / meta.strength)


def apply_decay(
    fields: list[UserField],
    now: datetime,
    prune_threshold: float = 0.1,
) -> list[UserField]:
    """Return fields whose retention is above the prune threshold."""
    return [
        f for f in fields if compute_retention(f.meta, now) >= prune_threshold
    ]


def reinforce_field(field: UserField, now: datetime) -> UserField:
    """Return a new UserField with reinforced decay metadata."""
    return UserField(
        key=field.key,
        value=field.value,
        meta=FieldMeta(
            last_accessed=now,
            access_count=field.meta.access_count + 1,
            strength=field.meta.strength + 0.5,
        ),
    )
