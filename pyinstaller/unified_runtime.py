"""Build-time helper for the PyInstaller specs.

The Unified Runtime — the ``mistralai_vibe_local_harness`` Python package and
its native ``_native`` extension — is a required component of every frozen
Vibe executable. ``vibe.spec``, ``vibe-acp.spec``, and ``vibe-app-server.spec``
collect it through this helper unconditionally, and the build fails when it
cannot be collected, so a packaging error can never produce a binary that
silently falls back to the legacy harness.

On Windows the helper also collects ``winpty`` (pywinpty), which the Runtime
and the managed shell load through ``importlib.import_module`` and PyInstaller
therefore cannot discover. A Windows build without it fails instead of
producing a binary that silently falls back to the non-PTY shell backend.
"""

from __future__ import annotations

from importlib import import_module, util
import sys
from typing import Any, NoReturn

_RUNTIME_PACKAGE = "mistralai_vibe_local_harness"
_RUNTIME_NATIVE = f"{_RUNTIME_PACKAGE}._native"
_RUNTIME_VIBE = f"{_RUNTIME_PACKAGE}.vibe"
_WINDOWS_PTY_PACKAGE = "winpty"


class UnifiedRuntimeRequiredError(SystemExit):
    """The build cannot produce a frozen executable without the Runtime."""


def _fail_build(reason: str) -> NoReturn:
    raise UnifiedRuntimeRequiredError(
        f"The Unified Runtime is a required component of every frozen Vibe "
        f"executable, but {reason}. The Runtime Python package and its native "
        f"extension ship inside the combined mistral-vibe wheel; install it "
        f"with 'uv sync --no-dev --group build' and rebuild."
    )


def require_unified_runtime() -> None:
    """Fail the build unless the required Runtime can be collected.

    Mirrors the runtime ``require_experimental_harness()`` probe: the frozen
    executable must be able to construct the Unified Runtime Host without a
    harness-selection flag, so the distribution, native extension, host
    factory, and host contract are enforced at build time instead of
    discovered dynamically at launch. ``create_harness_host`` is a pure Python
    construction with no native side effects, so the probe host is simply
    dropped.
    """
    if util.find_spec(_RUNTIME_PACKAGE) is None:
        _fail_build(f"the {_RUNTIME_PACKAGE!r} package is not installed")
    if util.find_spec(_RUNTIME_NATIVE) is None:
        _fail_build(f"the {_RUNTIME_NATIVE!r} native extension is not installed")
    try:
        runtime_vibe = import_module(_RUNTIME_VIBE)
    except Exception as exc:
        _fail_build(f"importing {_RUNTIME_VIBE!r} failed: {type(exc).__name__}: {exc}")
    host_factory = getattr(runtime_vibe, "create_harness_host", None)
    if not callable(host_factory):
        _fail_build(
            f"{_RUNTIME_VIBE!r} has no create_harness_host factory, so the "
            "frozen executable could not construct the Unified Runtime Host"
        )
    try:
        candidate_host = host_factory()
    except Exception as exc:
        _fail_build(
            f"constructing the Unified Runtime Host failed: {type(exc).__name__}: {exc}"
        )
    if not hasattr(candidate_host, "configure_hook_handlers"):
        _fail_build(
            f"the runtime host is incompatible: {type(candidate_host).__name__} "
            "has no configure_hook_handlers"
        )


def require_windows_pty() -> None:
    try:
        import_module(_WINDOWS_PTY_PACKAGE)
    except Exception as exc:
        raise UnifiedRuntimeRequiredError(
            f"Windows builds must bundle pywinpty for the PTY shell backend, but "
            f"importing {_WINDOWS_PTY_PACKAGE!r} failed: {type(exc).__name__}: "
            f"{exc}. Install it with 'uv sync --no-dev --group build' and rebuild."
        ) from exc


def collect_unified_runtime(
    platform: str = sys.platform,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Collect the Runtime, its native extension, and winpty on Windows, or fail."""
    require_unified_runtime()
    if platform == "win32":
        require_windows_pty()
    # PyInstaller is only importable in a packaging environment
    # (uv sync --group build), so it stays a lazy, dev-env-optional import.
    from PyInstaller.utils.hooks import (  # pyright: ignore[reportMissingModuleSource]
        collect_all,
    )

    runtime = collect_all(_RUNTIME_PACKAGE)
    if platform != "win32":
        return runtime
    windows_pty = collect_all(_WINDOWS_PTY_PACKAGE)
    return (
        runtime[0] + windows_pty[0],
        runtime[1] + windows_pty[1],
        runtime[2] + windows_pty[2],
    )
