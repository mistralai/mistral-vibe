from __future__ import annotations

import argparse
from collections.abc import Callable
from importlib import import_module, util
from typing import cast

_HARNESS_DISTRIBUTION_MODULE = "mistralai_rust_harness"
_VIBE_HARNESS_MODULE = "mistralai_rust_harness.vibe"


class ExperimentalHarnessUnavailableError(RuntimeError):
    pass


def experimental_harness_available() -> bool:
    try:
        return util.find_spec(_HARNESS_DISTRIBUTION_MODULE) is not None
    except ModuleNotFoundError:
        return False


def add_experimental_harness_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--experimental-harness",
        action="store_true",
        default=False,
        help=(
            "Use the experimental Unified Harness backend. Requires an "
            "internal Unified Harness installation."
            if experimental_harness_available()
            else argparse.SUPPRESS
        ),
    )


def create_experimental_harness_host() -> object:
    try:
        module = import_module(_VIBE_HARNESS_MODULE)
        factory = cast(Callable[[], object], module.create_harness_host)
    except (AttributeError, ModuleNotFoundError) as exc:
        raise ExperimentalHarnessUnavailableError(
            "The Unified Harness backend is not installed. Enable the internal "
            "local Harness wiring before using --experimental-harness."
        ) from exc
    return factory()
