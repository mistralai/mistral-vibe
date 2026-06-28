from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import count

from vibe.core.config.types import ConfigChangeCallback, ConfigChangeEvent


@dataclass(frozen=True, slots=True)
class Subscription:
    """Represent a subscription to config change events."""

    keys: frozenset[str] | None
    callback: ConfigChangeCallback


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, Subscription] = {}
        self._subscription_ids = count()

    def subscribe(
        self, callback: ConfigChangeCallback, *, keys: set[str] | None = None
    ) -> Callable[[], None]:
        """Register a listener and return a callable that unsubscribes it.

        keys filters delivery to slash-separated config paths (ancestor and
        descendant paths match); None subscribes to every change (wildcard).
        """
        subscription_id = next(self._subscription_ids)
        frozen_keys = frozenset(keys) if keys is not None else None
        self._subscribers[subscription_id] = Subscription(
            callback=callback, keys=frozen_keys
        )

        def unsubscribe() -> None:
            self._subscribers.pop(subscription_id, None)

        return unsubscribe

    def publish(self, event: ConfigChangeEvent) -> None:
        for subscription in list(self._subscribers.values()):
            if subscription.keys is None or any(
                _key_matches(key, changed)
                for key in subscription.keys
                for changed in event.changed_keys
            ):
                subscription.callback(event)


def _key_matches(subscription_key: str, changed_key: str) -> bool:
    # Match a key against its ancestor or descendant paths: "models" matches
    # "models/models" and vice versa, but "model" never matches "models".
    return (
        subscription_key == changed_key
        or changed_key.startswith(f"{subscription_key}/")
        or subscription_key.startswith(f"{changed_key}/")
    )
