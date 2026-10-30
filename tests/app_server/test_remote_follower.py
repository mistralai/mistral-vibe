from __future__ import annotations

import pytest

from vibe.app_server._remote_follower import RemoteFollower


class _Held:
    """The value a process service holds, as a follower sees it."""

    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.checks = 0

    def held(self) -> str | None:
        return self.value

    def check(self) -> None:
        self.checks += 1


def test_a_value_the_session_was_built_from_is_not_due() -> None:
    follower = RemoteFollower(_Held("a"), loaded="a")

    assert not follower.due
    assert follower.take() is None


def test_a_changed_value_is_due_until_loaded() -> None:
    held = _Held("a")
    follower = RemoteFollower(held, loaded="a")

    held.value = "b"
    assert follower.due
    assert follower.take() == "b"

    follower.mark_loaded("b")
    assert not follower.due


def test_a_change_reverted_before_the_boundary_is_not_due() -> None:
    held = _Held("a")
    follower = RemoteFollower(held, loaded="a")
    held.value = "b"

    held.value = "a"

    assert not follower.due


def test_nothing_held_yet_is_never_due() -> None:
    follower = RemoteFollower(_Held(None), loaded=None)

    assert not follower.due


def test_a_refused_value_is_not_due_again_until_it_changes() -> None:
    held = _Held("b")
    follower = RemoteFollower(held, loaded="a")

    follower.mark_loaded("b")
    assert not follower.due

    held.value = "c"
    assert follower.due


@pytest.mark.asyncio
async def test_a_refresh_makes_the_value_held_then_due_even_if_loaded() -> None:
    # /reload: what the value is checked against may have changed.
    held = _Held("a")
    follower = RemoteFollower(held, loaded="a")
    fetched: list[str] = []

    async def fetch() -> None:
        fetched.append("fetch")

    await follower.refresh(fetch)

    assert fetched == ["fetch"]
    assert follower.take() == "a"
    follower.mark_loaded("a")
    assert not follower.due


def test_check_starts_the_services_refresh() -> None:
    held = _Held("a")
    follower = RemoteFollower(held, loaded="a")

    follower.check()

    assert held.checks == 1
