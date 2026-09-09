from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from setproctitle import getproctitle

import pytest_vibe


@pytest.fixture(autouse=True)
def _clear_worker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    for var in pytest_vibe._CI_RUNNER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize(
    ("cpu_count", "expected_worker_count"),
    [(1, 1), (2, 1), (3, 1), (4, 1), (5, 2), (6, 2), (8, 3)],
)
def test_default_worker_count(cpu_count: int, expected_worker_count: int) -> None:
    assert pytest_vibe._default_worker_count(cpu_count) == expected_worker_count


def test_local_run_throttles_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytest_vibe, "_available_cpu_count", lambda: 8)

    assert pytest_vibe.pytest_xdist_auto_num_workers() == 3


@pytest.mark.parametrize("runner_var", pytest_vibe._CI_RUNNER_ENV_VARS)
@pytest.mark.parametrize("runner_value", ["1", "true", "TRUE", "yes"])
def test_ci_run_uses_all_available_cpus(
    monkeypatch: pytest.MonkeyPatch, runner_var: str, runner_value: str
) -> None:
    monkeypatch.setenv(runner_var, runner_value)
    monkeypatch.setattr(pytest_vibe, "_available_cpu_count", lambda: 8)

    assert pytest_vibe.pytest_xdist_auto_num_workers() == 8


def test_generic_ci_var_does_not_unthrottle(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``CI`` leaks into ordinary developer shells; it must not trigger the
    # all-cores path, or local runs would saturate the machine.
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(pytest_vibe, "_available_cpu_count", lambda: 8)

    assert pytest_vibe.pytest_xdist_auto_num_workers() == 3


def test_xdist_workers_do_not_update_process_titles() -> None:
    assert not getproctitle().startswith("[pytest-xdist ")


def test_only_local_controller_lowers_process_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    niceness_increments: list[int] = []
    monkeypatch.setattr(
        pytest_vibe.os, "nice", niceness_increments.append, raising=False
    )

    pytest_vibe.pytest_configure(cast(pytest.Config, SimpleNamespace()))
    pytest_vibe.pytest_configure(
        cast(
            pytest.Config,
            SimpleNamespace(
                workerinput={}, pluginmanager=SimpleNamespace(get_plugins=set)
            ),
        )
    )
    monkeypatch.setenv("BUILDKITE", "true")
    pytest_vibe.pytest_configure(cast(pytest.Config, SimpleNamespace()))

    assert niceness_increments == [pytest_vibe._LOCAL_NICENESS]


def test_worker_count_uses_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "4")

    worker_count = pytest_vibe.pytest_xdist_auto_num_workers()

    assert worker_count == 4


def test_environment_override_wins_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILDKITE", "true")
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "2")
    monkeypatch.setattr(pytest_vibe, "_available_cpu_count", lambda: 8)

    assert pytest_vibe.pytest_xdist_auto_num_workers() == 2


@pytest.mark.parametrize("configured_worker_count", ["0", "-1", "invalid"])
def test_worker_count_ignores_invalid_environment_override(
    monkeypatch: pytest.MonkeyPatch, configured_worker_count: str
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", configured_worker_count)
    monkeypatch.setattr(pytest_vibe, "_available_cpu_count", lambda: 8)

    with pytest.warns(UserWarning, match="must be a positive integer"):
        worker_count = pytest_vibe.pytest_xdist_auto_num_workers()

    assert worker_count == 3
