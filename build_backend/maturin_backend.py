"""Build one Vibe wheel containing both Rust delivery artifacts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any

import maturin

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_HARNESS_ROOT = _PROJECT_ROOT.parent / "vibe_sdk" / "harness"
_PUBLIC_HARNESS_ROOT = _PROJECT_ROOT / "harness"
_BUILD_ROOT = _PROJECT_ROOT / ".native-build"
_STAGED_CORE = _BUILD_ROOT / "harness-core"
_HARNESS_TARGET = _BUILD_ROOT / "harness-target"
_RUNTIME_PACKAGE = Path("runtimes/python/python/mistralai_vibe_local_harness")
_BUNDLED_RUNTIME = _PROJECT_ROOT / "mistralai_vibe_local_harness"
_TUI_MANIFEST = _PROJECT_ROOT / "vibe" / "cli-rust" / "Cargo.toml"
_TUI_TARGET = _BUILD_ROOT / "vibe-rs-target"
_TUI_NAME = "vibe-rs.exe" if sys.platform == "win32" else "vibe-rs"
_BUNDLED_TUI = _PROJECT_ROOT / "vibe" / "_bin" / _TUI_NAME


class NativeBuildError(RuntimeError):
    """The combined native wheel inputs cannot be prepared."""


def get_requires_for_build_wheel(
    config_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    requirements = maturin.get_requires_for_build_wheel(config_settings)
    if sys.platform.startswith("linux"):
        requirements.append("ziglang==0.16.0")
    return requirements


def get_requires_for_build_editable(
    config_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    return maturin.get_requires_for_build_editable(config_settings)


def get_requires_for_build_sdist(
    config_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    del config_settings
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: Mapping[str, Any] | None = None
) -> str:
    with _wheel_harness(), _maturin_environment():
        return maturin.prepare_metadata_for_build_wheel(
            metadata_directory, config_settings
        )


def prepare_metadata_for_build_editable(
    metadata_directory: str, config_settings: Mapping[str, Any] | None = None
) -> str:
    _stage_harness()
    with _maturin_environment():
        return maturin.prepare_metadata_for_build_editable(
            metadata_directory, config_settings
        )


def build_wheel(
    wheel_directory: str,
    config_settings: Mapping[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    with _wheel_harness(), _maturin_environment(portable_linux_wheel=True):
        _stage_rust_cli()
        return maturin.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_editable(
    wheel_directory: str,
    config_settings: Mapping[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _stage_harness()
    with _maturin_environment():
        return maturin.build_editable(
            wheel_directory, config_settings, metadata_directory
        )


def build_sdist(
    sdist_directory: str, config_settings: Mapping[str, Any] | None = None
) -> str:
    del sdist_directory, config_settings
    raise NativeBuildError("mistral-vibe publishes platform wheels only")


def _harness_root() -> Path:
    for candidate in (_PUBLIC_HARNESS_ROOT, _DASHBOARD_HARNESS_ROOT):
        if (candidate / "core" / "Cargo.toml").is_file():
            return candidate
    raise NativeBuildError("Unified Harness source is missing")


def _stage_harness() -> None:
    harness_root = _harness_root()
    _stage_core(harness_root)
    source_runtime = _runtime_source(harness_root)
    if source_runtime is None:
        return
    _replace_runtime(source_runtime)


def _stage_core(harness_root: Path) -> None:
    source_core = harness_root / "core"
    toolchain = harness_root / "rust-toolchain.toml"
    if not toolchain.is_file():
        raise NativeBuildError(f"Harness Rust toolchain pin is missing: {toolchain}")
    shutil.rmtree(_STAGED_CORE, ignore_errors=True)
    _STAGED_CORE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_core,
        _STAGED_CORE,
        symlinks=True,
        ignore=shutil.ignore_patterns("target", ".cache"),
    )
    shutil.copy2(toolchain, _STAGED_CORE / toolchain.name)


def _runtime_source(harness_root: Path) -> Path | None:
    source_runtime = harness_root / _RUNTIME_PACKAGE
    if source_runtime.is_dir():
        return source_runtime
    if _BUNDLED_RUNTIME.is_dir():
        return None
    raise NativeBuildError(f"Harness Python Runtime is missing: {source_runtime}")


def _copy_runtime(source_runtime: Path, destination: Path) -> None:
    shutil.copytree(
        source_runtime,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_native.*"),
    )


def _prepare_runtime(source_runtime: Path) -> tuple[Path, Path]:
    _BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    swap_root = Path(tempfile.mkdtemp(prefix="runtime-swap-", dir=_BUILD_ROOT))
    staged_runtime = swap_root / "staged"
    try:
        _copy_runtime(source_runtime, staged_runtime)
    except Exception:
        shutil.rmtree(swap_root, ignore_errors=True)
        raise
    return swap_root, staged_runtime


def _replace_runtime(source_runtime: Path) -> None:
    swap_root, staged_runtime = _prepare_runtime(source_runtime)
    backup = swap_root / "original"
    moved_original = False
    activation_started = False
    try:
        if _BUNDLED_RUNTIME.is_dir():
            shutil.move(_BUNDLED_RUNTIME, backup)
            moved_original = True
        activation_started = True
        shutil.move(staged_runtime, _BUNDLED_RUNTIME)
    except Exception:
        if activation_started:
            shutil.rmtree(_BUNDLED_RUNTIME, ignore_errors=True)
        if moved_original:
            shutil.move(backup, _BUNDLED_RUNTIME)
        raise
    finally:
        shutil.rmtree(swap_root, ignore_errors=True)


@contextmanager
def _wheel_harness() -> Iterator[None]:
    harness_root = _harness_root()
    _stage_core(harness_root)
    source_runtime = _runtime_source(harness_root)
    if source_runtime is None:
        yield
        return

    swap_root, staged_runtime = _prepare_runtime(source_runtime)
    backup = swap_root / "original"
    had_bundled_runtime = _BUNDLED_RUNTIME.is_dir()
    moved_original = False
    activation_started = False
    try:
        if had_bundled_runtime:
            shutil.move(_BUNDLED_RUNTIME, backup)
            moved_original = True
        activation_started = True
        shutil.move(staged_runtime, _BUNDLED_RUNTIME)
        yield
    finally:
        if activation_started:
            shutil.rmtree(_BUNDLED_RUNTIME, ignore_errors=True)
        if moved_original:
            shutil.move(backup, _BUNDLED_RUNTIME)
        shutil.rmtree(swap_root, ignore_errors=True)


def _stage_rust_cli() -> None:
    if os.environ.get("VIBE_SKIP_RUST_TUI"):
        return

    environment = os.environ.copy()
    environment["CARGO_TARGET_DIR"] = str(_TUI_TARGET)
    command = [
        "cargo",
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        str(_TUI_MANIFEST),
        "--bin",
        "vibe-rs",
        *shlex.split(os.environ.get("CARGO_BUILD_FLAGS", "")),
    ]
    try:
        subprocess.run(command, cwd=_PROJECT_ROOT, env=environment, check=True)
    except FileNotFoundError as error:
        raise NativeBuildError("Cargo is required to build the Vibe wheel") from error

    built = _TUI_TARGET / "release" / _TUI_NAME
    if not built.is_file():
        raise NativeBuildError(f"Rust CLI build did not produce {built}")
    staged = _TUI_TARGET.parent / f"staged-{_TUI_NAME}"
    staged.unlink(missing_ok=True)
    _BUNDLED_TUI.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(built, staged)
        staged.replace(_BUNDLED_TUI)
    finally:
        staged.unlink(missing_ok=True)


@contextmanager
def _maturin_environment(*, portable_linux_wheel: bool = False) -> Iterator[None]:
    original_arguments = os.environ.get("MATURIN_PEP517_ARGS")
    original_target = os.environ.get("CARGO_TARGET_DIR")
    original_strip = os.environ.get("CARGO_PROFILE_RELEASE_STRIP")
    arguments = shlex.split(original_arguments or "")
    if "--locked" not in arguments and "--frozen" not in arguments:
        arguments.append("--locked")
    has_compatibility = any(
        argument == "--compatibility" or argument.startswith("--compatibility=")
        for argument in arguments
    )
    if (
        portable_linux_wheel
        and sys.platform.startswith("linux")
        and not has_compatibility
    ):
        arguments.extend(["--compatibility", "manylinux_2_28"])
    if (
        portable_linux_wheel
        and sys.platform.startswith("linux")
        and "--zig" not in arguments
    ):
        arguments.append("--zig")
    os.environ["MATURIN_PEP517_ARGS"] = shlex.join(arguments)
    os.environ["CARGO_TARGET_DIR"] = str(_HARNESS_TARGET)
    # rustc strips Mach-O in-process instead of calling Apple's strip(1), and its
    # writer puts the symbol string table straight after an odd-sized indirect
    # symbol table, leaving it 4-byte aligned. dyld on macOS 26+ requires 8 and
    # refuses the image, so the built extension cannot be imported at all.
    # rust-lang/rust#157750. Drop this once the pinned toolchain carries the fix.
    if sys.platform == "darwin" and original_strip is None:
        os.environ["CARGO_PROFILE_RELEASE_STRIP"] = "none"
    try:
        yield
    finally:
        if original_arguments is None:
            os.environ.pop("MATURIN_PEP517_ARGS", None)
        else:
            os.environ["MATURIN_PEP517_ARGS"] = original_arguments
        if original_target is None:
            os.environ.pop("CARGO_TARGET_DIR", None)
        else:
            os.environ["CARGO_TARGET_DIR"] = original_target
        if original_strip is None:
            os.environ.pop("CARGO_PROFILE_RELEASE_STRIP", None)
        else:
            os.environ["CARGO_PROFILE_RELEASE_STRIP"] = original_strip
