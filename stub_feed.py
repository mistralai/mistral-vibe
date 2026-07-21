"""Dev fixture: stream a scripted Pawgress island_state sequence as JSONL on stdout.

Lets Person B build the PySide6 overlay with zero dependency on the Goal backend:

    uv run python stub_feed.py | uv run python -m vibe.overlay

It replays the demo story: reproduce -> patch -> verify 3/5 (fail) -> retry -> 5/5 -> done.
"""

from __future__ import annotations

import sys
import time
from typing import TypedDict

from vibe.core.pawgress.events import Criterion, IslandState, IslandStatus, encode_jsonl

GOAL = "Fix the flaky cache test"
BUDGET = 2.0
CONTEXT_MAX = 200_000
USAGE_LIMIT = 500_000


class Stats(TypedDict):
    context_tokens: int
    context_max: int
    usage_used: int
    usage_limit: int
    usage_reset_seconds: int


def stats(ctx_tokens: int, usage_used: int, reset: int) -> Stats:
    return {
        "context_tokens": ctx_tokens,
        "context_max": CONTEXT_MAX,
        "usage_used": usage_used,
        "usage_limit": USAGE_LIMIT,
        "usage_reset_seconds": reset,
    }


def emit(state: IslandState, hold: float = 1.2) -> None:
    sys.stdout.write(encode_jsonl(state))
    sys.stdout.flush()
    time.sleep(hold)


def criteria(
    reproduced: bool, patched: bool, verify: str | None, reviewed: bool
) -> list[Criterion]:
    return [
        Criterion(label="Reproduced", done=reproduced),
        Criterion(label="Patch implemented", done=patched),
        Criterion(
            label="Verification",
            done=verify == "done",
            progress=verify if verify not in {None, "done"} else None,
        ),
        Criterion(label="Final review", done=reviewed),
    ]


def main() -> None:
    emit(
        IslandState(
            goal=GOAL,
            state=IslandStatus.WORKING,
            detail="Reproducing the flaky test",
            criteria=criteria(False, False, None, False),
            iteration="1/8",
            elapsed="0m18s",
            cost=0.04,
            budget=BUDGET,
            **stats(6_000, 40_000, 11_400),
        )
    )

    emit(
        IslandState(
            goal=GOAL,
            state=IslandStatus.WORKING,
            detail="Editing cache.py",
            criteria=criteria(True, False, None, False),
            iteration="1/8",
            elapsed="1m02s",
            cost=0.11,
            budget=BUDGET,
            **stats(14_000, 120_000, 11_220),
        )
    )

    for i in range(1, 4):
        emit(
            IslandState(
                goal=GOAL,
                state=IslandStatus.VERIFYING,
                detail=f"Testing {i}/5",
                criteria=criteria(True, True, f"{i}/5", False),
                iteration="1/8",
                elapsed=f"1m{20 + i * 8}s",
                cost=0.14 + i * 0.03,
                budget=BUDGET,
                **stats(14_000 + i * 4_000, 120_000 + i * 30_000, 11_100 - i * 60),
            ),
            hold=0.9,
        )

    emit(
        IslandState(
            goal=GOAL,
            state=IslandStatus.WORKING,
            detail="3/5 passed · trying another fix",
            criteria=criteria(True, False, "3/5", False),
            iteration="2/8",
            elapsed="2m41s",
            cost=0.31,
            budget=BUDGET,
            **stats(34_000, 90_000, 10_980),
        ),
        hold=1.6,
    )

    for i in range(1, 6):
        emit(
            IslandState(
                goal=GOAL,
                state=IslandStatus.VERIFYING,
                detail=f"Testing {i}/5",
                criteria=criteria(True, True, f"{i}/5", False),
                iteration="2/8",
                elapsed=f"3m{i * 7}s",
                cost=0.40 + i * 0.02,
                budget=BUDGET,
                **stats(34_000 + i * 3_000, 90_000 + i * 40_000, 10_800 - i * 60),
            ),
            hold=0.9,
        )

    emit(
        IslandState(
            goal=GOAL,
            state=IslandStatus.COMPLETED,
            detail="Goal complete",
            criteria=criteria(True, True, "done", True),
            iteration="2/8",
            elapsed="3m58s",
            cost=0.52,
            budget=BUDGET,
            **stats(60_000, 300_000, 10_620),
            evidence=[
                "5/5 repeated runs passed",
                "128 tests passed",
                "No public API changes",
            ],
        ),
        hold=5.0,
    )


if __name__ == "__main__":
    main()
