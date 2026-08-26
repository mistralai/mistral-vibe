from __future__ import annotations

import pytest

import pytest_vibe


@pytest.mark.parametrize(
    ("cpu_count", "expected_worker_count"),
    [(1, 1), (2, 1), (3, 1), (4, 1), (5, 2), (6, 2), (8, 3)],
)
def test_default_worker_count(cpu_count: int, expected_worker_count: int) -> None:
    assert pytest_vibe._default_worker_count(cpu_count) == expected_worker_count


def test_worker_count_uses_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "4")

    worker_count = pytest_vibe.pytest_xdist_auto_num_workers()

    assert worker_count == 4


@pytest.mark.parametrize("configured_worker_count", ["0", "-1", "invalid"])
def test_worker_count_ignores_invalid_environment_override(
    monkeypatch: pytest.MonkeyPatch, configured_worker_count: str
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", configured_worker_count)
    monkeypatch.setattr(pytest_vibe, "_available_cpu_count", lambda: 8)

    with pytest.warns(UserWarning, match="must be a positive integer"):
        worker_count = pytest_vibe.pytest_xdist_auto_num_workers()

    assert worker_count == 3
