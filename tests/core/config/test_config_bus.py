from __future__ import annotations

from vibe.core.config.event_bus import EventBus
from vibe.core.config.types import ConfigChangeEvent


def _make_event(changed: set[str]) -> ConfigChangeEvent:
    return ConfigChangeEvent(
        changed_keys=frozenset(changed), before={}, after={}, reason=""
    )


def test_wildcard_subscriber_fires_on_every_event() -> None:
    bus = EventBus()
    received: list[ConfigChangeEvent] = []
    bus.subscribe(received.append)

    event = _make_event({"value"})
    bus.publish(event)

    assert received == [event]


def test_keyed_subscriber_filters_by_key() -> None:
    bus = EventBus()
    received: list[ConfigChangeEvent] = []
    bus.subscribe(received.append, keys={"value"})

    bus.publish(_make_event({"other"}))
    assert received == []

    bus.publish(_make_event({"value", "other"}))
    assert len(received) == 1


def test_ancestor_descendant_keys_match_bidirectionally() -> None:
    bus = EventBus()
    received: list[ConfigChangeEvent] = []
    bus.subscribe(received.append, keys={"models"})
    bus.subscribe(received.append, keys={"models/models"})

    bus.publish(_make_event({"models/models"}))
    bus.publish(_make_event({"models"}))

    assert len(received) == 4


def test_partial_segment_is_not_a_prefix_match() -> None:
    bus = EventBus()
    received: list[ConfigChangeEvent] = []
    bus.subscribe(received.append, keys={"model"})

    bus.publish(_make_event({"models"}))

    assert received == []


def test_sibling_keys_do_not_match() -> None:
    bus = EventBus()
    received: list[ConfigChangeEvent] = []
    bus.subscribe(received.append, keys={"model/other1"})

    bus.publish(_make_event({"model/other2"}))

    assert received == []


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[ConfigChangeEvent] = []
    unsubscribe = bus.subscribe(received.append)
    unsubscribe()

    bus.publish(_make_event({"value"}))

    assert received == []


def test_unsubscribe_during_publish_is_safe() -> None:
    bus = EventBus()
    calls: list[str] = []

    def first(_: ConfigChangeEvent) -> None:
        calls.append("first")
        unsubscribe_second()

    bus.subscribe(first)
    unsubscribe_second = bus.subscribe(lambda _: calls.append("second"))

    bus.publish(_make_event({"value"}))
    bus.publish(_make_event({"value"}))

    assert calls == ["first", "second", "first"]
