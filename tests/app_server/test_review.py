from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import create_test_app_server_session
from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode
from vibe.app_server.review import (
    ReviewAgentOwner,
    ReviewAllTarget,
    ReviewFileTarget,
    ReviewLastTurnsTarget,
    ReviewRegionRef,
    ReviewRegionsTarget,
    ReviewRegionTarget,
    ReviewScopeFileTarget,
    ReviewScopeTarget,
    ReviewTarget,
    TextReviewRegion,
)
from vibe.core.checkpoints import AgentTurn, FileSnapshot, FileState
from vibe.core.review import (
    AllTarget,
    FileTarget,
    LastTurnsTarget,
    RegionsTarget,
    RegionTarget,
    ReviewTarget as CoreReviewTarget,
    ScopeFileTarget,
    ScopeTarget,
)
from vibe.core.types import UserMessageEvent


@pytest.mark.asyncio
async def test_review_resource_projects_shared_checkpoint_state(tmp_path: Path) -> None:
    path = tmp_path / "review.txt"
    path.write_text("before\n", encoding="utf-8")
    loop = build_test_agent_loop()
    owner = ReviewAgentOwner(turn_id=len(loop.messages))
    loop.checkpoint_recorder.create_checkpoint()
    loop.checkpoint_recorder.add_snapshot(
        FileSnapshot(path=str(path.resolve()), state=FileState(path.read_bytes()))
    )
    path.write_text("after\n", encoding="utf-8")
    loop.checkpoint_recorder.seal_turn()

    session = await create_test_app_server_session(loop)
    try:
        state = await session.resources.review.state()
        baseline = await session.resources.review.baseline(str(path.resolve()))
        turn_diff = await session.resources.review.turn_diff(str(path.resolve()), owner)
        hunks = await session.resources.review.hunks(str(path.resolve()), owner)
    finally:
        await session.close()

    assert len(state.files) == 1
    assert state.files[0].path == str(path.resolve())
    assert state.files[0].status == "modified"
    assert len(state.files[0].regions) == 1
    region = state.files[0].regions[0]
    assert isinstance(region, TextReviewRegion)
    assert region.owner == owner
    assert state.scopes[0].owner == owner
    assert state.scopes[0].files[0].region_count == 1
    assert baseline.content == "before\n"
    assert turn_diff.model_dump(mode="json", by_alias=True) == {
        "status": "modified",
        "baseline": "before\n",
        "current": "after\n",
    }
    assert len(hunks.hunks) == 1
    assert hunks.hunks[0].regions == [
        ReviewRegionRef(version_index=region.version_index, ordinal=region.ordinal)
    ]


@pytest.mark.parametrize(
    ("operation", "target", "expected"),
    [
        (
            "approve",
            ReviewRegionTarget(path="a.py", version_index=1, ordinal=0),
            RegionTarget(path="a.py", version_index=1, ordinal=0),
        ),
        (
            "approve",
            ReviewRegionsTarget(
                path="a.py",
                regions=[
                    ReviewRegionRef(version_index=0, ordinal=0),
                    ReviewRegionRef(version_index=1, ordinal=0),
                ],
            ),
            RegionsTarget(path="a.py", regions=((0, 0), (1, 0))),
        ),
        (
            "revert",
            ReviewScopeTarget(owner=ReviewAgentOwner(turn_id=3)),
            ScopeTarget(owner=AgentTurn(3)),
        ),
        (
            "revert",
            ReviewScopeFileTarget(owner=ReviewAgentOwner(turn_id=3), path="a.py"),
            ScopeFileTarget(owner=AgentTurn(3), path="a.py"),
        ),
        ("revert", ReviewFileTarget(path="a.py"), FileTarget(path="a.py")),
        ("revert", ReviewAllTarget(), AllTarget()),
        ("approve", ReviewLastTurnsTarget(count=2), LastTurnsTarget(count=2)),
    ],
)
@pytest.mark.asyncio
async def test_review_resource_mutation_targets(
    operation: str, target: ReviewTarget, expected: CoreReviewTarget
) -> None:
    loop = build_test_agent_loop()
    with patch.object(
        loop.review_manager, f"{operation}_review", return_value=[]
    ) as mutation:
        session = await create_test_app_server_session(loop)
        try:
            if operation == "approve":
                await session.resources.review.approve(target)
            else:
                await session.resources.review.revert(target)
        finally:
            await session.close()

    mutation.assert_called_once_with(expected)


@pytest.mark.asyncio
async def test_review_mutation_rejects_active_turn() -> None:
    loop = build_test_agent_loop()
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()

    async def blocking_act(message: str, **_kwargs):
        turn_started.set()
        await release_turn.wait()
        yield UserMessageEvent(content=message, message_id="user-message")

    loop.act = blocking_act
    with patch.object(loop.review_manager, "revert_review", return_value=[]) as revert:
        session = await create_test_app_server_session(loop)

        async def consume_turn() -> None:
            async for _ in session.act("wait"):
                pass

        turn = asyncio.create_task(consume_turn())
        try:
            await turn_started.wait()
            with pytest.raises(AppServerResponseError) as error:
                await session.resources.review.revert(ReviewAllTarget())
            assert error.value.error.code is ProtocolErrorCode.CONFLICT
            revert.assert_not_called()
        finally:
            release_turn.set()
            await turn
            await session.close()
