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
import platform
import re
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
# The default wheel's manylinux glibc, and where vibe-rs is linked for it.
_MANYLINUX_GLIBC = (2, 28)
_ZIG_WRAPPERS = _BUILD_ROOT / "vibe-rs-zig"
_ZIG_ARCHITECTURES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}
# Maturin's platform tag option and its alias.
_COMPATIBILITY_FLAGS = frozenset({"--compatibility", "--manylinux"})
# Maturin's legacy manylinux tags and the glibc each allows (PEP 600).
_LEGACY_MANYLINUX_GLIBC = {
    "manylinux1": (2, 5),
    "manylinux2010": (2, 12),
    "manylinux2014": (2, 17),
}
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
_VOICE_OPT_IN = (
    "To enable voice mode, install pkg-config and the ALSA development package "
    "(Debian/Ubuntu: libasound2-dev, Fedora/RHEL: alsa-lib-devel, Arch: alsa-lib), "
    "and build a wheel for this machine only: set "
    'MATURIN_PEP517_ARGS="--compatibility linux", run uv cache clean '
    "mistral-vibe, then reinstall.\n"
    "Set CARGO_BUILD_FLAGS to choose the Cargo features explicitly."
)
_ALSA_MISSING_WARNING = (
    "warning: ALSA development files were not found through pkg-config, so the "
    f"Vibe terminal UI is built without voice mode.\n{_VOICE_OPT_IN}"
)
_MANYLINUX_NO_VOICE_WARNING = (
    "warning: this Linux wheel is built for glibc {major}.{minor} and later "
    "(manylinux), so the Vibe terminal UI is built without voice mode: voice "
    "links the system ALSA library, which a portable wheel cannot carry.\n"
    + _VOICE_OPT_IN
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
    build_flags = _rust_cli_build_flags()
    _link_rust_cli_with_zig(environment, build_flags)
    command = [
        "cargo",
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        str(_TUI_MANIFEST),
        "--bin",
        "vibe-rs",
        *build_flags,
    ]
    try:
        subprocess.run(command, cwd=_PROJECT_ROOT, env=environment, check=True)
    except FileNotFoundError as error:
        raise NativeBuildError(_CARGO_MISSING) from error

    # Cargo writes a --target build under target/<triple>/ instead.
    triple = _cargo_target_triple(build_flags) or os.environ.get("CARGO_BUILD_TARGET")
    release_dir = _TUI_TARGET / triple if triple else _TUI_TARGET
    built = release_dir / "release" / _TUI_NAME
    if not built.is_file():
        raise NativeBuildError(f"Rust CLI build did not produce {built}")
    promised = _wheel_manylinux_glibc()
    if promised is not None:
        floor = _glibc_floor(built.read_bytes())
        if floor is not None and floor > promised:
            raise NativeBuildError(
                f"{built} needs glibc {floor[0]}.{floor[1]}, newer than the "
                f"wheel's manylinux_{promised[0]}_{promised[1]} tag allows"
            )
    staged = _TUI_TARGET.parent / f"staged-{_TUI_NAME}"
    staged.unlink(missing_ok=True)
    _BUNDLED_TUI.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(built, staged)
        staged.replace(_BUNDLED_TUI)
    finally:
        staged.unlink(missing_ok=True)


def _link_rust_cli_with_zig(
    environment: dict[str, str], build_flags: list[str]
) -> None:
    """Link vibe-rs for the manylinux glibc when Maturin links the Harness so.

    Plain cargo links against the build machine's glibc, so a wheel built on a
    newer Linux would carry a vibe-rs that the manylinux tag (manylinux_2_28 by
    default) promises to older hosts but cannot run there. On a Linux host,
    this builds it for the host's gnu target and the tag's glibc with Maturin's
    zig wrappers, as `maturin --zig` does for the Harness.
    """
    arch = _zig_rust_cli_arch(build_flags)
    if arch is None:
        return
    maturin_command = shutil.which("maturin")
    if maturin_command is None:
        raise NativeBuildError("Linking vibe-rs with zig needs `maturin` on PATH")
    triple = f"{arch}-unknown-linux-gnu"
    # Maturin's zig links for the wheel's manylinux tag, so vibe-rs does too.
    glibc = _wheel_manylinux_glibc() or _MANYLINUX_GLIBC
    zig_target = f"{arch}-linux-gnu.{glibc[0]}.{glibc[1]}"
    _ZIG_WRAPPERS.mkdir(parents=True, exist_ok=True)
    tools = {}
    for tool, arguments in (
        ("cc", f"cc -- -target {zig_target}"),
        ("c++", f"c++ -- -target {zig_target}"),
        ("ar", "ar --"),
    ):
        wrapper = _ZIG_WRAPPERS / tool
        wrapper.write_text(
            f'#!/bin/sh\nexec {shlex.quote(maturin_command)} zig {arguments} "$@"\n'
        )
        wrapper.chmod(0o755)
        tools[tool] = str(wrapper)
    target = triple.replace("-", "_")
    environment[f"CARGO_TARGET_{target.upper()}_LINKER"] = tools["cc"]
    environment[f"CC_{target}"] = tools["cc"]
    environment[f"CXX_{target}"] = tools["c++"]
    environment[f"AR_{target}"] = tools["ar"]
    build_flags.extend(["--target", triple])


def _zig_rust_cli_arch(build_flags: list[str]) -> str | None:
    """The architecture to link vibe-rs for with zig, or None without zig.

    Zig links it in a Linux build with Maturin's `--zig`, on x86_64 or
    aarch64, for the host's gnu target: no cargo target is set.
    """
    if not sys.platform.startswith("linux"):
        return None
    if "--zig" not in shlex.split(os.environ.get("MATURIN_PEP517_ARGS", "")):
        return None
    if _cargo_target_triple(build_flags) or os.environ.get("CARGO_BUILD_TARGET"):
        return None
    return _ZIG_ARCHITECTURES.get(platform.machine().lower())


def _wheel_manylinux_glibc() -> tuple[int, int] | None:
    """The glibc the wheel's manylinux tag lets a Linux build need, if tagged.

    Read from the Maturin arguments, so it covers vibe-rs however it was
    linked: with zig, or with plain cargo for a `--target` or unknown machine.
    """
    if not sys.platform.startswith("linux"):
        return None
    arguments = shlex.split(os.environ.get("MATURIN_PEP517_ARGS", ""))
    if (
        "--zig" in arguments
        and not _has_compatibility(arguments)
        and platform.machine().lower() in _ZIG_ARCHITECTURES
    ):
        # Maturin's `--zig` then tags it manylinux2014, its x86_64/aarch64 minimum.
        return _LEGACY_MANYLINUX_GLIBC["manylinux2014"]
    tags = []
    for argument in arguments:
        tag = re.sub(r"^--(?:compatibility|manylinux)=", "", argument)
        if match := re.fullmatch(r"manylinux_(\d+)_(\d+)", tag):
            tags.append((int(match[1]), int(match[2])))
        elif tag in _LEGACY_MANYLINUX_GLIBC:
            tags.append(_LEGACY_MANYLINUX_GLIBC[tag])
    return min(tags, default=None)


def _has_compatibility(arguments: list[str]) -> bool:
    return any(
        argument in _COMPATIBILITY_FLAGS
        or argument.startswith(tuple(f"{flag}=" for flag in _COMPATIBILITY_FLAGS))
        for argument in arguments
    )


def _glibc_floor(binary: bytes) -> tuple[int, int] | None:
    """The newest glibc symbol version an ELF binary needs, if any."""
    versions = {
        (int(major), int(minor))
        for major, minor in re.findall(rb"GLIBC_(\d+)\.(\d+)", binary)
    }
    return max(versions, default=None)


def _rust_cli_build_flags() -> list[str]:
    # The default `voice` feature links ALSA on Linux through pkg-config. Without
    # its development files, build like the published Linux wheels instead of
    # failing, unless the caller already chose the features. A manylinux wheel
    # linked with zig never gets voice: zig links for the tag's older glibc and
    # cannot link the host's libasound, which needs the host's glibc.
    build_flags = shlex.split(os.environ.get("CARGO_BUILD_FLAGS", ""))
    if _selects_cargo_features(build_flags) or not _is_native_linux_build(build_flags):
        return build_flags
    if _zig_rust_cli_arch(build_flags) is not None:
        major, minor = _wheel_manylinux_glibc() or _MANYLINUX_GLIBC
        print(
            _MANYLINUX_NO_VOICE_WARNING.format(major=major, minor=minor),
            file=sys.stderr,
        )
        return [*build_flags, "--no-default-features"]
    if _alsa_available():
        return build_flags
    print(_ALSA_MISSING_WARNING, file=sys.stderr)
    return [*build_flags, "--no-default-features"]


def _selects_cargo_features(arguments: list[str]) -> bool:
    return any(
        argument in {"--no-default-features", "--all-features"}
        or argument.startswith(("--features", "-F"))
        for argument in arguments
    )


def _is_native_linux_build(build_flags: list[str]) -> bool:
    # Builds with an explicit target keep the default features and are left to
    # CARGO_BUILD_FLAGS: the pkg-config crate reads per-target PKG_CONFIG_*
    # variables that this host-side probe does not emulate.
    if not sys.platform.startswith("linux"):
        return False
    return not (
        _cargo_target_triple(build_flags) or os.environ.get("CARGO_BUILD_TARGET")
    )


def _alsa_available() -> bool:
    # Mirrors how the pkg-config crate used by alsa-sys picks its binary:
    # $PKG_CONFIG if set, otherwise pkg-config, then pkgconf if pkg-config
    # cannot be run. Its PKG_CONFIG_<target> and HOST_/TARGET_PKG_CONFIG
    # overrides are not emulated.
    override = os.environ.get("PKG_CONFIG")
    candidates = [override] if override is not None else ["pkg-config", "pkgconf"]
    for executable in candidates:
        try:
            result = subprocess.run(
                [executable, "--exists", "alsa"], check=False, capture_output=True
            )
        except OSError:
            continue
        return result.returncode == 0
    return False


def _cargo_target_triple(arguments: list[str]) -> str | None:
    for index, argument in enumerate(arguments):
        if argument.startswith("--target="):
            return argument.removeprefix("--target=")
        if argument == "--target" and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


@contextmanager
def _maturin_environment(*, portable_linux_wheel: bool = False) -> Iterator[None]:
    original_arguments = os.environ.get("MATURIN_PEP517_ARGS")
    original_target = os.environ.get("CARGO_TARGET_DIR")
    original_strip = os.environ.get("CARGO_PROFILE_RELEASE_STRIP")
    arguments = shlex.split(original_arguments or "")
    if "--locked" not in arguments and "--frozen" not in arguments:
        arguments.append("--locked")
    if (
        portable_linux_wheel
        and sys.platform.startswith("linux")
        and not _has_compatibility(arguments)
    ):
        arguments.extend(["--compatibility", "manylinux_2_28"])
    native_linux_wheel = any(
        argument in {"--compatibility=linux", "--manylinux=linux"}
        or (
            argument in _COMPATIBILITY_FLAGS
            and arguments[index + 1 : index + 2] == ["linux"]
        )
        for index, argument in enumerate(arguments)
    )
    if (
        portable_linux_wheel
        and sys.platform.startswith("linux")
        and not native_linux_wheel
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
