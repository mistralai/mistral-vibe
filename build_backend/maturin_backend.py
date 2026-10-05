"""Build one Vibe wheel containing both Rust delivery artifacts.

An editable install with ``VIBE_EXTERNAL_HARNESS=1`` leaves the Harness out
instead: it builds pure-Python ``vibe`` from source and expects
``mistralai-vibe-local-harness`` to be installed separately, as a wheel built
from ``agents/harness/harness/runtimes/python`` -- the same package this backend
otherwise bundles. CI uses it so one job compiles the Harness and every Vibe
job installs that wheel, instead of each job compiling it during ``uv sync``.
Wheel builds ignore the variable: a wheel always carries the Harness.

A wheel build with ``VIBE_PREBUILT_HARNESS_WHEEL`` and ``VIBE_PREBUILT_RUST_TUI``
compiles nothing: it packages pure-Python ``vibe`` with the Harness wheel's
contents and the given vibe-rs binary, in the layout the Maturin build produces.
CI builds and caches each Rust artifact by its own sources, so a Python-only
change repackages them instead of recompiling.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import hashlib
import importlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from types import ModuleType
from typing import IO, Any
import zipfile

if os.name == "nt":
    import msvcrt
else:
    import fcntl

import maturin

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_HARNESS_ROOT = _PROJECT_ROOT.parent / "agents" / "harness" / "harness"
_PUBLIC_HARNESS_ROOT = _PROJECT_ROOT / "harness"
_BUILD_ROOT = _PROJECT_ROOT / ".native-build"
_STAGED_CORE = _BUILD_ROOT / "harness-core"
_HARNESS_TARGET = _BUILD_ROOT / "harness-target"
_RUNTIME_PACKAGE = Path("runtimes/python/python/mistralai_vibe_local_harness")
_BUNDLED_RUNTIME = _PROJECT_ROOT / "mistralai_vibe_local_harness"
# Keep in sync with key_inputs in tools/buildkite-pipeline/steps/units/vibe_sdist.py.
_SDIST_HARNESS_PARTS = (
    "core/Cargo.toml",
    "core/Cargo.lock",
    "core/src",
    "rust-toolchain.toml",
    _RUNTIME_PACKAGE.as_posix(),
)
_TUI_MANIFEST = _PROJECT_ROOT / "vibe" / "cli-rust" / "Cargo.toml"
_TUI_TARGET = _BUILD_ROOT / "vibe-rs-target"
_TUI_NAME = "vibe-rs.exe" if sys.platform == "win32" else "vibe-rs"
_BUNDLED_TUI = _PROJECT_ROOT / "vibe" / "_bin" / _TUI_NAME
_EXTERNAL_HARNESS_ENV = "VIBE_EXTERNAL_HARNESS"
_PREBUILT_HARNESS_ENV = "VIBE_PREBUILT_HARNESS_WHEEL"
_PREBUILT_TUI_ENV = "VIBE_PREBUILT_RUST_TUI"
# Same range as the repository's other uv_build projects.
_UV_BUILD_REQUIREMENT = "uv_build>=0.9.7,<0.10.0"
# Exact pin: uv_build builds the published sdist.
_UV_BUILD_SDIST_REQUIREMENT = "uv_build==0.9.30"
_BUILD_LOCK_NAME = "build.lock"
_TUI_WHEEL_ENTRY = f"vibe/_bin/{_TUI_NAME}"
_RUNTIME_WHEEL_MARKERS = (
    "mistralai_vibe_local_harness/__init__.py",
    "mistralai_vibe_local_harness/protocol.py",
    "mistralai_vibe_local_harness/runtime.py",
    "mistralai_vibe_local_harness/session_protocol.py",
    "mistralai_vibe_local_harness/vibe/__init__.py",
)
_CARGO_MISSING = (
    "Building Vibe from source requires the Rust toolchain, but `cargo` was not "
    "found on PATH. Install it with rustup, then open a new shell and retry:\n\n"
    "    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh\n\n"
    "On Windows, download rustup-init.exe from https://rustup.rs instead.\n"
    "See https://rust-lang.org/tools/install/ and CONTRIBUTING.md "
    '("Building distributions") for details.'
)


class NativeBuildError(RuntimeError):
    """The combined native wheel inputs cannot be prepared."""


def get_requires_for_build_wheel(
    config_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    if _prebuilt_natives() is not None:
        return [_UV_BUILD_REQUIREMENT]
    requirements = maturin.get_requires_for_build_wheel(config_settings)
    if sys.platform.startswith("linux") and not _from_sdist():
        requirements.append("ziglang==0.16.0")
    return requirements


def get_requires_for_build_editable(
    config_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    if _external_harness():
        return [_UV_BUILD_REQUIREMENT]
    return maturin.get_requires_for_build_editable(config_settings)


def get_requires_for_build_sdist(
    config_settings: Mapping[str, Any] | None = None,
) -> list[str]:
    del config_settings
    return [_UV_BUILD_SDIST_REQUIREMENT]


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings: Mapping[str, Any] | None = None
) -> str:
    if _prebuilt_natives() is not None:
        return _uv_build().prepare_metadata_for_build_wheel(
            metadata_directory, config_settings
        )
    _require_cargo()
    with _wheel_harness(), _maturin_environment():
        return maturin.prepare_metadata_for_build_wheel(
            metadata_directory, config_settings
        )


def prepare_metadata_for_build_editable(
    metadata_directory: str, config_settings: Mapping[str, Any] | None = None
) -> str:
    if _external_harness():
        return _uv_build().prepare_metadata_for_build_editable(
            metadata_directory, config_settings
        )
    _require_cargo()
    with _exclusive_build_lock():
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
    prebuilt = _prebuilt_natives()
    if prebuilt is not None:
        return _assemble_wheel(*prebuilt, Path(wheel_directory), config_settings)
    _require_cargo()
    with _wheel_harness(), _maturin_environment(portable_linux_wheel=not _from_sdist()):
        _stage_rust_cli()
        wheel_name = maturin.build_wheel(
            wheel_directory, config_settings, metadata_directory
        )
    _validate_wheel_contents(Path(wheel_directory) / wheel_name)
    return wheel_name


def build_editable(
    wheel_directory: str,
    config_settings: Mapping[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    if _external_harness():
        return _uv_build().build_editable(
            wheel_directory, config_settings, metadata_directory
        )
    _require_cargo()
    with _exclusive_build_lock():
        _stage_harness()
        with _maturin_environment():
            return maturin.build_editable(
                wheel_directory, config_settings, metadata_directory
            )


def _external_harness() -> bool:
    # Exactly "1", so a stray "0" or "false" keeps the default combined build.
    return os.environ.get(_EXTERNAL_HARNESS_ENV) == "1"


def _from_sdist() -> bool:
    # Only an unpacked sdist has PKG-INFO; such local builds (e.g. Homebrew)
    # need neither zig nor a manylinux tag.
    return (_PROJECT_ROOT / "PKG-INFO").is_file()


def _uv_build() -> ModuleType:
    # Resolved at call time: only sdist, external-Harness editable and
    # prebuilt-native wheel builds list uv_build as a requirement.
    return importlib.import_module("uv_build")


def _prebuilt_natives() -> tuple[Path, Path] | None:
    harness_wheel = os.environ.get(_PREBUILT_HARNESS_ENV)
    if not harness_wheel:
        return None
    rust_tui = os.environ.get(_PREBUILT_TUI_ENV)
    if not rust_tui:
        raise NativeBuildError(
            f"{_PREBUILT_HARNESS_ENV} also needs {_PREBUILT_TUI_ENV}"
        )
    return Path(harness_wheel), Path(rust_tui)


def _assemble_wheel(
    harness_wheel: Path,
    rust_tui: Path,
    wheel_directory: Path,
    config_settings: Mapping[str, Any] | None,
) -> str:
    """Package pure-Python vibe with prebuilt Harness and vibe-rs artifacts."""
    tag = harness_wheel.name.removesuffix(".whl").split("-", 2)[2]
    with tempfile.TemporaryDirectory() as scratch:
        pure_name = _uv_build().build_wheel(scratch, config_settings)
        if not pure_name.endswith("-py3-none-any.whl"):
            raise NativeBuildError(f"expected a pure-Python wheel, got {pure_name}")
        name = pure_name.replace("-py3-none-any.whl", f"-{tag}.whl")
        with (
            zipfile.ZipFile(Path(scratch) / pure_name) as python,
            zipfile.ZipFile(harness_wheel) as harness,
            zipfile.ZipFile(wheel_directory / name, "w") as wheel,
        ):
            dist_info = next(
                path.split("/")[0]
                for path in python.namelist()
                if path.split("/")[0].endswith(".dist-info")
            )
            records: list[str] = []

            def add(path: str, data: bytes, mode: int = 0o644) -> None:
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = (0o100000 | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                wheel.writestr(info, data)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                records.append(
                    f"{path},sha256={digest.rstrip(b'=').decode()},{len(data)}"
                )

            # Maturin leaves the Rust sources out, and vibe-rs is added below.
            skipped = (
                "vibe/cli-rust/",
                "vibe/_bin/",
                f"{dist_info}/RECORD",
                f"{dist_info}/WHEEL",
            )
            for path in python.namelist():
                if not path.endswith("/") and not path.startswith(skipped):
                    add(path, python.read(path))
            for info in harness.infolist():
                if not info.is_dir() and ".dist-info/" not in info.filename:
                    mode = (info.external_attr >> 16) & 0o777 or 0o644
                    add(info.filename, harness.read(info), mode)
            add(f"vibe/_bin/{_TUI_NAME}", rust_tui.read_bytes(), 0o755)
            python_tag, abi_tag, platform_tag = tag.split("-")
            tags = [
                f"Tag: {py}-{abi}-{platform}"
                for py in python_tag.split(".")
                for abi in abi_tag.split(".")
                for platform in platform_tag.split(".")
            ]
            metadata = ["Wheel-Version: 1.0", "Generator: mistral-vibe"]
            metadata += ["Root-Is-Purelib: false", *tags, ""]
            add(f"{dist_info}/WHEEL", "\n".join(metadata).encode())
            wheel.writestr(
                f"{dist_info}/RECORD",
                "\n".join([*records, f"{dist_info}/RECORD,,", ""]),
            )
    return name


def build_sdist(
    sdist_directory: str, config_settings: Mapping[str, Any] | None = None
) -> str:
    harness = _harness_root()
    with tempfile.TemporaryDirectory() as scratch:
        name = _uv_build().build_sdist(scratch, config_settings)
        root = name.removesuffix(".tar.gz")
        with (
            tarfile.open(Path(scratch) / name) as base,
            tarfile.open(Path(sdist_directory) / name, "w:gz") as sdist,
        ):
            for member in base.getmembers():
                sdist.addfile(member, base.extractfile(member))
            # Same harness/ layout and file set the public release sync writes.
            for part in _SDIST_HARNESS_PARTS:
                source_only = (
                    _runtime_source_only
                    if part == _RUNTIME_PACKAGE.as_posix()
                    else _source_only
                )
                sdist.add(harness / part, f"{root}/harness/{part}", filter=source_only)
    return name


def _source_only(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if member.name.endswith(("/__pycache__", ".pyc", ".so", ".pyd")):
        return None
    return member


def _runtime_source_only(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if member.isdir() or member.name.endswith((".py", ".pyi", "/py.typed")):
        return member
    return None


def _require_cargo() -> None:
    # Without MATURIN_NO_INSTALL_RUST, maturin installs a temporary Rust toolchain.
    if os.environ.get("MATURIN_NO_INSTALL_RUST") and shutil.which("cargo") is None:
        raise NativeBuildError(_CARGO_MISSING)


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
    if (_BUNDLED_RUNTIME / "__init__.py").is_file():
        return None
    raise NativeBuildError(
        f"Harness Python runtime is missing: no source at {source_runtime} and "
        f"no complete bundled copy at {_BUNDLED_RUNTIME}. Restore the Harness "
        "source, or rebuild from a complete checkout"
    )


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


def _validate_wheel_contents(wheel: Path) -> None:
    if not wheel.is_file():
        raise NativeBuildError(f"Maturin did not produce {wheel}")
    with zipfile.ZipFile(wheel) as archive:
        names = frozenset(archive.namelist())
    required: list[str] = list(_RUNTIME_WHEEL_MARKERS)
    if not os.environ.get("VIBE_SKIP_RUST_TUI"):
        required.append(_TUI_WHEEL_ENTRY)
    missing = [entry for entry in required if entry not in names]
    if missing:
        raise NativeBuildError(
            f"Combined wheel is missing bundled components {', '.join(missing)} "
            f"in {wheel}: a concurrent or interrupted build clobbered the staged "
            "artifacts. Recover with: uv cache clean mistral-vibe, then reinstall; "
            "rebuilds restage the runtime from source automatically"
        )


@contextmanager
def _exclusive_build_lock() -> Iterator[None]:
    # The runtime swap moves directories in place while maturin reads them, so
    # two builds sharing one checkout must not overlap: the loser's restore
    # deletes the winner's staged package mid-build and ships a stripped wheel.
    _BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    with open(_BUILD_ROOT / _BUILD_LOCK_NAME, "a+b") as lock_file:
        _acquire_build_lock(lock_file)
        try:
            yield
        finally:
            _release_build_lock(lock_file)


def _acquire_build_lock(lock_file: IO[bytes]) -> None:
    if os.name == "nt":
        # Seed byte 0 like the other msvcrt lock helpers, so the locked range
        # exists. The CRT emulates append as seek-then-write, so a racing
        # build can land this write on byte 0 after another build locked it;
        # skip the seed then and let the lock loop below wait for it.
        try:
            if os.fstat(lock_file.fileno()).st_size == 0:
                os.write(lock_file.fileno(), b"\0")
        except PermissionError:
            pass
        while True:
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except PermissionError:
                # EACCES is contention: another build holds the lock. Any
                # other error is real and must not spin forever.
                time.sleep(0.5)
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_build_lock(lock_file: IO[bytes]) -> None:
    if os.name == "nt":
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _wheel_harness() -> Iterator[None]:
    with _exclusive_build_lock():
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
        raise NativeBuildError(_CARGO_MISSING) from error

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
