from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.app_server._goal_state import create_goal
from vibe.app_server._goal_store import (
    GOAL_FILENAME,
    UnifiedGoalStore,
    read_stored_goal,
)


def test_a_goal_with_a_non_ascii_objective_reads_back(tmp_path: Path) -> None:
    """*Prepare*: A goal whose objective is not ASCII, saved as UTF-8.
    *Do*: Read it back without a live store, as session listings do.
    *Assert*: It reads, whatever the locale's codec.
    """
    # Prepare
    goal = create_goal(
        "Traduire « café » en 日本語 ✓", token_budget=None, max_budget=None, now_ms=1
    )
    path = tmp_path / GOAL_FILENAME
    path.write_bytes(
        (
            '{"format": "vibe.goal/v1", "goal": '
            + goal.model_dump_json(by_alias=True)
            + "}"
        ).encode()
    )

    # Do
    stored = read_stored_goal(path)

    # Assert
    assert stored == goal


@pytest.mark.asyncio
async def test_a_goal_saved_by_a_newer_build_still_restores(tmp_path: Path) -> None:
    """*Prepare*: A ``goal.json`` whose goal carries a field this build does not
    know, as a newer build may write.
    *Do*: Restore it in a store, and read it as a listing does.
    *Assert*: Both read the goal; the unknown field is dropped.
    """
    # Prepare
    goal = create_goal("Ship it", token_budget=None, max_budget=None, now_ms=1)
    saved = {**goal.model_dump(mode="json", by_alias=True), "futureField": 7}
    path = tmp_path / GOAL_FILENAME
    path.write_text(json.dumps({"format": "vibe.goal/v1", "goal": saved}))
    store = UnifiedGoalStore(path, persistent=lambda: True)

    # Do
    await store.restore()
    listed = read_stored_goal(path)

    # Assert
    assert store.goal == goal
    assert listed == goal
