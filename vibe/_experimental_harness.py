from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module, util
from typing import Literal, cast

_HARNESS_DISTRIBUTION_MODULE = "mistralai_vibe_local_harness"
_VIBE_HARNESS_MODULE = "mistralai_vibe_local_harness.vibe"


class ExperimentalHarnessUnavailableError(RuntimeError):
    """The Unified Harness Runtime cannot load.

    Raised at startup instead of falling back to the legacy harness: with the
    Unified Harness as the required default runtime, a missing, incompatible,
    or failed Runtime is a packaging or platform error, and silently running
    a second engine would reintroduce the fallback this hard default removes.
    """


def experimental_harness_available() -> bool:
    try:
        return util.find_spec(_HARNESS_DISTRIBUTION_MODULE) is not None
    except ModuleNotFoundError:
        return False


def runtime_startup_error(reason: str) -> ExperimentalHarnessUnavailableError:
    """The actionable startup error for a Runtime that cannot load or start."""
    return ExperimentalHarnessUnavailableError(
        f"The Unified Harness runtime is required to start Vibe but could not "
        f"be loaded: {reason}.\n"
        "The runtime ships with the Vibe distribution. Reinstall or upgrade "
        "Vibe (for example 'uv tool upgrade mistral-vibe'), or pass "
        "--legacy-harness for the temporary legacy escape hatch."
    )


def require_experimental_harness() -> None:
    """Fail startup when the Unified Harness Runtime cannot load or start.

    Entry points that cannot propagate a mid-session failure into a clean
    process abort — ACP, whose library converts a handler exception into a
    per-session JSON-RPC error and keeps serving — call this before serving
    so every failure mode aborts with the actionable message: a missing
    distribution, a native extension that cannot import, an incompatible
    pinned harness (missing factory or builtin-hook registry), or a failed
    initialization. The probe constructs a candidate Host through the same
    path the session runtime uses; ``create_harness_host`` is a pure Python
    construction with no native side effects, so the probe host is simply
    dropped.
    """
    if not experimental_harness_available():
        raise runtime_startup_error(
            f"the {_HARNESS_DISTRIBUTION_MODULE!r} package is not installed"
        )
    candidate = create_experimental_harness_host()
    if not hasattr(candidate, "configure_hook_handlers"):
        raise runtime_startup_error(
            f"the runtime host is incompatible: {type(candidate).__name__} "
            "has no configure_hook_handlers"
        )


def add_experimental_harness_argument(
    parser: argparse.ArgumentParser,
    *,
    group: argparse._MutuallyExclusiveGroup | None = None,
) -> None:
    target: argparse.ArgumentParser | argparse._MutuallyExclusiveGroup = group or parser
    target.add_argument(
        "--experimental-harness",
        action="store_true",
        default=False,
        help=(
            "Use the Unified Harness backend. It is now the default runtime, "
            "so this flag is redundant and kept only until the legacy "
            "harness cutover removes it."
            if experimental_harness_available()
            else argparse.SUPPRESS
        ),
    )


def add_smart_approve_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--smart-approve",
        action="store_true",
        default=False,
        help=(
            "Classify each tool call and auto-run the safe ones, prompting "
            "only for risky ones."
            if experimental_harness_available()
            else argparse.SUPPRESS
        ),
    )


HarnessSelectionSource = Literal[
    "flag-legacy",  # --legacy-harness escape hatch
    "flag",  # --experimental-harness (redundant with the default)
    "default",  # unflagged startup: the Unified Harness
]


@dataclass(frozen=True)
class HarnessSelection:
    """The resolved harness decision, combining the CLI flags.

    The GrowthBook ``vibe_cli_unified_harness_rollout`` assignment is no
    longer consulted: the Unified Harness is the deterministic default, and
    a missing, expired, or absent rollout cache entry must not select a
    different engine. A missing or incompatible Runtime fails startup
    instead of falling back.
    """

    use_unified: bool
    source: HarnessSelectionSource


def resolve_harness_selection(
    *, experimental_harness: bool, legacy_harness: bool
) -> HarnessSelection:
    """Resolve which harness to use, combining the CLI flags.

    Precedence (highest wins):
      1. ``--legacy-harness``  -> legacy (escape hatch until the cutover)
      2. Default and ``--experimental-harness`` -> unified, unconditionally
    """
    if legacy_harness:
        return HarnessSelection(use_unified=False, source="flag-legacy")

    return HarnessSelection(
        use_unified=True, source="flag" if experimental_harness else "default"
    )


def create_experimental_harness_host() -> object:
    try:
        module = import_module(_VIBE_HARNESS_MODULE)
        factory = cast(Callable[[], object], module.create_harness_host)
    except (AttributeError, ImportError, ModuleNotFoundError) as exc:
        raise runtime_startup_error(f"{type(exc).__name__}: {exc}") from exc
    # Smart approve needs no Host-global registration: the Runtime tool gate
    # runs the classifier whenever a tool's mode is "classify", which the
    # session's adapter config carries (and can flip live when the mode
    # changes).
    try:
        return factory()
    except ExperimentalHarnessUnavailableError:
        raise
    except Exception as exc:
        raise runtime_startup_error(
            f"initialization failed with {type(exc).__name__}: {exc}"
        ) from exc
