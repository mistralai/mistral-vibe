"""``user_plan`` is derived from the emitted experiment-attribute snapshot.

Regression for `vibe.new_session` events that carried a populated
`experiment_attributes.planName` but a null top-level `user_plan`: the two were
maintained as separate stores (`_user_plan` vs the manager snapshot) and could
diverge (e.g. a scoped reset cleared the field while the snapshot kept the
plan, or a key-missing reinit skipped the snapshot update).

Consolidation: when an attribute snapshot exists it is authoritative for
`user_plan`, so it can never disagree with the emitted `experiment_attributes`.
The legacy `_user_plan` field remains only as a fallback for paths that resolve
a plan without a snapshot (e.g. `account/read` before experiments init).
"""

from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.models import ExperimentAttributes
from vibe.setup.auth.whoami import NO_PLAN_DATA


def _attrs(*, plan_type: str | None, plan_name: str | None) -> ExperimentAttributes:
    return ExperimentAttributes(
        userId="be326535-df00-4d1b-b4dd-0ac07796cd8f",
        entrypoint="acp",
        harness=ExperimentSurface.LEGACY,
        agent_version="2.24.4",
        os="linux",
        planType=plan_type,
        planName=plan_name,
    )


@pytest.mark.asyncio
async def test_user_plan_derives_from_snapshot_when_present() -> None:
    loop = build_test_agent_loop()
    try:
        loop.experiment_manager.set_attributes(
            _attrs(plan_type="chat", plan_name="FREE")
        )
        assert loop.user_plan == "Free"
    finally:
        await loop.aclose()


@pytest.mark.asyncio
async def test_snapshot_is_authoritative_over_stale_field() -> None:
    # The event-3 divergence: snapshot has the plan, the legacy field is null.
    loop = build_test_agent_loop()
    try:
        loop.experiment_manager.set_attributes(
            _attrs(plan_type="chat", plan_name="FREE")
        )
        loop.set_user_plan(None)
        assert loop.user_plan == "Free"
    finally:
        await loop.aclose()


@pytest.mark.asyncio
async def test_user_plan_falls_back_to_field_without_snapshot() -> None:
    # account/read can resolve a plan before experiments init stamps a snapshot.
    loop = build_test_agent_loop()
    try:
        assert loop.experiment_manager.attributes() is None
        loop.set_user_plan("Pro")
        assert loop.user_plan == "Pro"
    finally:
        await loop.aclose()


@pytest.mark.asyncio
async def test_user_plan_sentinel_from_snapshot() -> None:
    loop = build_test_agent_loop()
    try:
        loop.experiment_manager.set_attributes(
            _attrs(plan_type=NO_PLAN_DATA, plan_name=NO_PLAN_DATA)
        )
        assert loop.user_plan == NO_PLAN_DATA
    finally:
        await loop.aclose()


@pytest.mark.asyncio
async def test_user_plan_none_when_snapshot_plan_unresolved() -> None:
    # Snapshot exists but whoami didn't resolve a plan -> null (tried, failed).
    loop = build_test_agent_loop()
    try:
        loop.experiment_manager.set_attributes(_attrs(plan_type=None, plan_name=None))
        loop.set_user_plan("stale-should-be-ignored")
        assert loop.user_plan is None
    finally:
        await loop.aclose()
