from __future__ import annotations

import math
import os
import warnings

import pytest

_WORKER_COUNT_ENV_VAR = "PYTEST_XDIST_AUTO_NUM_WORKERS"


def _available_cpu_count() -> int:
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        try:
            return len(sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


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

    return _default_worker_count(_available_cpu_count())
