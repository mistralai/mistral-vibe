from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.pawgress.verifier import VerificationResult, run_verification


def test_verification_result_passed_requires_all_runs():
    assert VerificationResult(total=5, pass_count=5).passed
    assert not VerificationResult(total=5, pass_count=4).passed
    assert not VerificationResult(total=0, pass_count=0).passed


@pytest.mark.asyncio
async def test_run_verification_all_passes_for_true_command():
    result = await run_verification("true", 3)

    assert result.total == 3
    assert result.pass_count == 3
    assert result.passed


@pytest.mark.asyncio
async def test_run_verification_no_passes_for_false_command():
    result = await run_verification("false", 3)

    assert result.total == 3
    assert result.pass_count == 0
    assert not result.passed


@pytest.mark.asyncio
async def test_run_verification_counts_partial_passes(tmp_path: Path):
    counter = tmp_path / "count"
    counter.write_text("0", encoding="utf-8")
    # Increments a persisted counter each run; exits 0 only for the first 2 runs.
    command = (
        f'n=$(cat "{counter}"); n=$((n + 1)); '
        f'printf "%s" "$n" > "{counter}"; [ "$n" -le 2 ]'
    )

    result = await run_verification(command, 5)

    assert result.total == 5
    assert result.pass_count == 2
    assert not result.passed
