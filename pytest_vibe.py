from __future__ import annotations

import math
import os
from pathlib import Path
import warnings

import pytest

_WORKER_COUNT_ENV_VAR = "PYTEST_XDIST_AUTO_NUM_WORKERS"
_LOCAL_NICENESS = 10
_CGROUP_ROOT = Path("/sys/fs/cgroup")
# cgroup v1 often co-mounts the cpu controller with cpuacct, and the plain
# ``cpu`` symlink to it is not guaranteed to exist.
_CGROUP_V1_CPU_CONTROLLERS = ("cpu", "cpu,cpuacct", "cpuacct,cpu")


def _read_cgroup_file(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _cpu_limit_from_quota(quota: int, period: int) -> int | None:
    if quota <= 0 or period <= 0:
        return None
    return max(1, math.ceil(quota / period))


def _cgroup_cpu_limit(root: Path = _CGROUP_ROOT) -> int | None:
    """Return the CPU quota of this process's cgroup, rounded up, if any.

    A container's CPU limit is a CFS quota, not an affinity mask: the scheduler
    still lets the process see every CPU of the node, so a 2-CPU Kubernetes pod
    on a 32-core node reports 32 from ``sched_getaffinity``.
    """
    if (cpu_max := _read_cgroup_file(root / "cpu.max")) is not None:
        quota, _, period = cpu_max.partition(" ")
        if quota == "max":
            return None
        try:
            return _cpu_limit_from_quota(int(quota), int(period or "100000"))
        except ValueError:
            pass

    for controller in _CGROUP_V1_CPU_CONTROLLERS:
        v1_quota = _read_cgroup_file(root / controller / "cpu.cfs_quota_us")
        v1_period = _read_cgroup_file(root / controller / "cpu.cfs_period_us")
        if v1_quota is None or v1_period is None:
            continue
        try:
            return _cpu_limit_from_quota(int(v1_quota), int(v1_period))
        except ValueError:
            continue
    return None


def _visible_cpu_count() -> int:
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        try:
            return len(sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def _available_cpu_count() -> int:
    visible = _visible_cpu_count()
    if (limit := _cgroup_cpu_limit()) is None:
        return visible
    return min(visible, limit)


# Runner-specific signals, not the generic ``CI`` variable. ``CI`` is routinely
# exported in developer shells (and inherited by every child process), so keying
# off it would unthrottle local runs and saturate the machine. Dedicated CI
# runners set one of these instead.
_CI_RUNNER_ENV_VARS = ("BUILDKITE", "GITHUB_ACTIONS")


def _is_ci() -> bool:
    return any(
        os.environ.get(var, "").lower() in {"1", "true", "yes"}
        for var in _CI_RUNNER_ENV_VARS
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        for plugin in config.pluginmanager.get_plugins():
            run_one_test = getattr(plugin, "run_one_test", None)
            if run_one_test and "worker_title" in run_one_test.__globals__:
                run_one_test.__globals__["worker_title"] = lambda _title: None
                break
        return
    if _is_ci():
        return
    if nice := getattr(os, "nice", None):
        nice(_LOCAL_NICENESS)


def _default_worker_count(cpu_count: int) -> int:
    return max(1, math.ceil(cpu_count / 2) - 1)


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers() -> int:
    configured_worker_count = os.environ.get(_WORKER_COUNT_ENV_VAR)
    if configured_worker_count is not None:
        try:
            worker_count = int(configured_worker_count)
        except ValueError:
            worker_count = 0

        if worker_count > 0:
            return worker_count

        warnings.warn(
            f"{_WORKER_COUNT_ENV_VAR} must be a positive integer; using the default",
            stacklevel=2,
        )

    cpu_count = _available_cpu_count()
    # Only throttle interactive/local runs to keep developer machines usable.
    # CI runs the full suite and needs every available core for parallelism.
    if _is_ci():
        return cpu_count
    return _default_worker_count(cpu_count)
