"""Jobs for tests of the background worker process, imported by the worker."""

from __future__ import annotations

import os
import time
from typing import Any

from vibe.app_server._background_worker_process import Job, JobSet


def _pid(_args: Any) -> int:
    return os.getpid()


def _crash(_args: Any) -> None:
    os._exit(3)


def _sleep(args: Any) -> float:
    time.sleep(args)
    return args


def _fail(_args: Any) -> None:
    raise ValueError("the job failed")


def _unserializable(_args: Any) -> object:
    return object()


def test_jobs() -> JobSet:
    return {
        "test.pid": Job(_pid),
        "test.crash": Job(_crash),
        "test.sleep": Job(_sleep),
        "test.fail": Job(_fail),
        "test.unserializable": Job(_unserializable),
    }
