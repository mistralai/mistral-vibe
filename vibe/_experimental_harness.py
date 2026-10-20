from __future__ import annotations

from collections.abc import Callable
from importlib import import_module, util
from typing import cast

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
        "Vibe (for example 'uv tool upgrade mistral-vibe')."
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
