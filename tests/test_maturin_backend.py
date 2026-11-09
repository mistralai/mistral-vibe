from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import ModuleType
import zipfile

import pytest

BACKEND_PATH = Path(__file__).parents[1] / "build_backend/maturin_backend.py"


def _load_backend(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # The Vibe CI lanes run with it set; each test opts in explicitly instead.
    monkeypatch.delenv("VIBE_EXTERNAL_HARNESS", raising=False)
    maturin = ModuleType("maturin")
    monkeypatch.setitem(sys.modules, "maturin", maturin)
    spec = importlib.util.spec_from_file_location(
        "vibe_combined_maturin_backend", BACKEND_PATH
    )
    assert spec is not None and spec.loader is not None
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


def _write_wheel(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for entry in entries:
            archive.writestr(entry, "stub")


def test_linux_wheel_build_environment_provides_zig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setattr(
        backend.maturin,
        "get_requires_for_build_wheel",
        lambda _settings: ["maturin-runtime"],
        raising=False,
    )

    assert backend.get_requires_for_build_wheel() == [
        "maturin-runtime",
        "ziglang==0.16.0",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        "--compatibility linux",
        "--compatibility=linux",
        "--manylinux linux",
        "--manylinux=linux",
    ],
)
def test_native_linux_intermediate_wheel_uses_the_image_toolchain(
    monkeypatch: pytest.MonkeyPatch, arguments: str
) -> None:
    """*Prepare*: A Linux image requests a wheel for local PyInstaller consumption.
    *Do*: Resolve the native build arguments.
    *Assert*: The image compiler is used and the explicit wheel tag is retained.
    """
    # Prepare
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setenv("MATURIN_PEP517_ARGS", arguments)

    # Do
    with backend._maturin_environment(portable_linux_wheel=True):
        resolved = os.environ["MATURIN_PEP517_ARGS"]

        # Assert
        assert "--zig" not in resolved
        assert arguments in resolved
        assert "--locked" in resolved
    assert os.environ["MATURIN_PEP517_ARGS"] == arguments


def test_stage_harness_prefers_public_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    public = tmp_path / "public-harness"
    dashboard = tmp_path / "dashboard-harness"
    staged_core = tmp_path / "staged-core"
    bundled_runtime = tmp_path / "mistralai_vibe_local_harness"
    runtime = public / backend._RUNTIME_PACKAGE
    (public / "core/src").mkdir(parents=True)
    runtime.mkdir(parents=True)
    (public / "core/Cargo.toml").write_text("[package]\nname='core'\n")
    (public / "core/src/lib.rs").write_text("// public\n")
    (public / "rust-toolchain.toml").write_text("[toolchain]\nchannel='1.97.1'\n")
    (public / "core/target/ignored").mkdir(parents=True)
    (runtime / "__init__.py").write_text("SOURCE = 'public'\n")
    (dashboard / "core").mkdir(parents=True)
    (dashboard / "core/Cargo.toml").write_text("[package]\nname='dashboard'\n")

    monkeypatch.setattr(backend, "_PUBLIC_HARNESS_ROOT", public)
    monkeypatch.setattr(backend, "_DASHBOARD_HARNESS_ROOT", dashboard)
    monkeypatch.setattr(backend, "_STAGED_CORE", staged_core)
    monkeypatch.setattr(backend, "_BUNDLED_RUNTIME", bundled_runtime)

    backend._stage_harness()

    assert (staged_core / "src/lib.rs").read_text() == "// public\n"
    assert "1.97.1" in (staged_core / "rust-toolchain.toml").read_text()
    assert not (staged_core / "target").exists()
    assert (bundled_runtime / "__init__.py").read_text() == "SOURCE = 'public'\n"


def test_stage_harness_accepts_public_root_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    public = tmp_path / "harness"
    staged_core = tmp_path / "staged-core"
    bundled_runtime = tmp_path / "mistralai_vibe_local_harness"
    (public / "core/src").mkdir(parents=True)
    (public / "core/Cargo.toml").write_text("[package]\nname='core'\n")
    (public / "core/src/lib.rs").write_text("// public\n")
    (public / "rust-toolchain.toml").write_text("[toolchain]\nchannel='1.97.1'\n")
    bundled_runtime.mkdir()
    (bundled_runtime / "__init__.py").write_text("SOURCE = 'public-root'\n")

    monkeypatch.setattr(backend, "_PUBLIC_HARNESS_ROOT", public)
    monkeypatch.setattr(backend, "_DASHBOARD_HARNESS_ROOT", tmp_path / "missing")
    monkeypatch.setattr(backend, "_STAGED_CORE", staged_core)
    monkeypatch.setattr(backend, "_BUNDLED_RUNTIME", bundled_runtime)

    backend._stage_harness()

    assert (staged_core / "src/lib.rs").read_text() == "// public\n"
    assert (bundled_runtime / "__init__.py").read_text() == "SOURCE = 'public-root'\n"


def test_stage_rust_cli_builds_into_private_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    project = tmp_path / "project"
    manifest = project / "vibe/cli-rust/Cargo.toml"
    target = project / ".native-build/vibe-rs-target"
    bundled = project / "vibe/_bin" / backend._TUI_NAME
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[package]\nname='vibe-rs'\n")
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> None:
        assert check
        calls.append((command, cwd, env))
        built = target / "release" / backend._TUI_NAME
        built.parent.mkdir(parents=True)
        built.write_bytes(b"rust-cli")

    monkeypatch.setattr(backend, "_PROJECT_ROOT", project)
    monkeypatch.setattr(backend, "_TUI_MANIFEST", manifest)
    monkeypatch.setattr(backend, "_TUI_TARGET", target)
    monkeypatch.setattr(backend, "_BUNDLED_TUI", bundled)
    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setenv("CARGO_BUILD_FLAGS", "--no-default-features")

    backend._stage_rust_cli()

    command, cwd, environment = calls[0]
    assert command[-1] == "--no-default-features"
    assert "--locked" in command
    assert cwd == project
    assert environment["CARGO_TARGET_DIR"] == str(target)
    assert bundled.read_bytes() == b"rust-cli"


@pytest.mark.parametrize(
    ("flags", "build_target"),
    [
        ("--target x86_64-apple-darwin", None),
        ("--target=x86_64-apple-darwin", None),
        ("", "x86_64-apple-darwin"),
    ],
)
def test_stage_rust_cli_finds_a_cross_target_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    flags: str,
    build_target: str | None,
) -> None:
    backend = _load_backend(monkeypatch)
    project = tmp_path / "project"
    manifest = project / "vibe/cli-rust/Cargo.toml"
    target = project / ".native-build/vibe-rs-target"
    bundled = project / "vibe/_bin" / backend._TUI_NAME
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[package]\nname='vibe-rs'\n")

    def run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> None:
        built = target / "x86_64-apple-darwin/release" / backend._TUI_NAME
        built.parent.mkdir(parents=True)
        built.write_bytes(b"cross-rust-cli")

    monkeypatch.setattr(backend, "_PROJECT_ROOT", project)
    monkeypatch.setattr(backend, "_TUI_MANIFEST", manifest)
    monkeypatch.setattr(backend, "_TUI_TARGET", target)
    monkeypatch.setattr(backend, "_BUNDLED_TUI", bundled)
    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setenv("CARGO_BUILD_FLAGS", flags)
    if build_target is None:
        monkeypatch.delenv("CARGO_BUILD_TARGET", raising=False)
    else:
        monkeypatch.setenv("CARGO_BUILD_TARGET", build_target)

    backend._stage_rust_cli()

    assert bundled.read_bytes() == b"cross-rust-cli"


def _stage_portable_rust_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    binary: bytes,
    *,
    host: str = "linux",
    built_triple: str | None = "x86_64-unknown-linux-gnu",
) -> tuple[ModuleType, list[tuple[list[str], dict[str, str]]], Path]:
    """Stage vibe-rs as a manylinux wheel build on x86_64 Linux does."""
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.sys, "platform", host)
    project = tmp_path / "project"
    target = project / ".native-build/vibe-rs-target"
    bundled = project / "vibe/_bin" / backend._TUI_NAME
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"previous-rust-cli")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> None:
        calls.append((command, env))
        built = target / (built_triple or "") / "release" / backend._TUI_NAME
        built.parent.mkdir(parents=True)
        built.write_bytes(binary)

    monkeypatch.setattr(backend, "_PROJECT_ROOT", project)
    monkeypatch.setattr(backend, "_TUI_TARGET", target)
    monkeypatch.setattr(backend, "_BUNDLED_TUI", bundled)
    monkeypatch.setattr(backend, "_ZIG_WRAPPERS", project / ".native-build/zig")
    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setattr(backend.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(backend.shutil, "which", lambda _name: "/build env/maturin")
    monkeypatch.setenv(
        "MATURIN_PEP517_ARGS", "--locked --compatibility manylinux_2_28 --zig"
    )
    monkeypatch.setenv("CARGO_BUILD_FLAGS", "--no-default-features")
    monkeypatch.delenv("CARGO_BUILD_TARGET", raising=False)
    return backend, calls, bundled


@pytest.mark.parametrize(
    ("arguments", "glibc"),
    [
        ("--locked --compatibility manylinux_2_28 --zig", "2.28"),
        ("--locked --compatibility manylinux2014 --zig", "2.17"),
        ("--locked --compatibility=manylinux_2_31 --zig", "2.31"),
        ("--locked --compatibility linux --zig", "2.28"),
        ("--locked --zig", "2.17"),
    ],
)
def test_portable_linux_rust_cli_is_linked_for_the_wheel_glibc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arguments: str, glibc: str
) -> None:
    """*Prepare*: A zig wheel build on x86_64 Linux for a manylinux tag, a
    `linux` tag or no tag, whose vibe-rs needs glibc 2.17 at most.
    *Do*: Stage vibe-rs.
    *Assert*: Cargo built it for the gnu target, linked and compiled with
    Maturin's zig wrappers for the tag's glibc (2.17, Maturin's zig default,
    without a tag; 2.28 for `linux`), and the binary was bundled.
    """
    # Prepare
    binary = b"\x7fELF\0GLIBC_2.2.5\0GLIBC_2.17\0"
    backend, calls, bundled = _stage_portable_rust_cli(monkeypatch, tmp_path, binary)
    monkeypatch.setenv("MATURIN_PEP517_ARGS", arguments)

    # Do
    backend._stage_rust_cli()

    # Assert
    [(command, environment)] = calls
    assert command[-3:] == [
        "--no-default-features",
        "--target",
        "x86_64-unknown-linux-gnu",
    ]
    linker = Path(environment["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER"])
    assert environment["CC_x86_64_unknown_linux_gnu"] == str(linker)
    assert linker.read_text() == (
        "#!/bin/sh\nexec '/build env/maturin' zig cc -- "
        f'-target x86_64-linux-gnu.{glibc} "$@"\n'
    )
    assert "zig c++ --" in Path(environment["CXX_x86_64_unknown_linux_gnu"]).read_text()
    assert "zig ar --" in Path(environment["AR_x86_64_unknown_linux_gnu"]).read_text()
    assert bundled.read_bytes() == binary


@pytest.mark.parametrize(
    ("arguments", "binary", "floor"),
    [
        (
            "--locked --compatibility manylinux_2_28 --zig",
            b"\x7fELF\0GLIBC_2.28\0GLIBC_2.34\0",
            "2.34",
        ),
        ("--locked --zig", b"\x7fELF\0GLIBC_2.17\0GLIBC_2.28\0", "2.28"),
    ],
)
def test_portable_linux_rust_cli_needing_a_newer_glibc_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: str,
    binary: bytes,
    floor: str,
) -> None:
    """*Prepare*: A manylinux_2_28 wheel build whose vibe-rs needs glibc 2.34,
    or a bare `--zig` build (Maturin's manylinux2014) whose vibe-rs needs 2.28.
    *Do*: Stage vibe-rs.
    *Assert*: The build fails naming the floor and keeps the previous binary.
    """
    # Prepare
    backend, _calls, bundled = _stage_portable_rust_cli(monkeypatch, tmp_path, binary)
    monkeypatch.setenv("MATURIN_PEP517_ARGS", arguments)

    # Do / Assert
    with pytest.raises(backend.NativeBuildError, match=f"needs glibc {floor}"):
        backend._stage_rust_cli()
    assert bundled.read_bytes() == b"previous-rust-cli"


@pytest.mark.parametrize(
    ("flags", "build_target"),
    [("--target x86_64-unknown-linux-gnu", None), ("", "x86_64-unknown-linux-gnu")],
)
def test_explicit_target_rust_cli_needing_a_newer_glibc_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    flags: str,
    build_target: str | None,
) -> None:
    """*Prepare*: A manylinux_2_28 wheel build with an explicit Cargo target,
    which links vibe-rs without zig, and a vibe-rs that needs glibc 2.34.
    *Do*: Stage vibe-rs.
    *Assert*: The wheel's glibc floor is still checked and the build fails.
    """
    # Prepare
    binary = b"\x7fELF\0GLIBC_2.28\0GLIBC_2.34\0"
    backend, calls, bundled = _stage_portable_rust_cli(monkeypatch, tmp_path, binary)
    monkeypatch.setenv("CARGO_BUILD_FLAGS", flags)
    if build_target is not None:
        monkeypatch.setenv("CARGO_BUILD_TARGET", build_target)

    # Do / Assert
    with pytest.raises(backend.NativeBuildError, match=r"needs glibc 2\.34"):
        backend._stage_rust_cli()
    [(_command, environment)] = calls
    assert "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER" not in environment
    assert bundled.read_bytes() == b"previous-rust-cli"


@pytest.mark.parametrize(
    ("arguments", "tag"),
    [
        ("--locked --compatibility manylinux_2_28 --zig", (2, 28)),
        ("--compatibility=manylinux_2_31", (2, 31)),
        ("--compatibility manylinux2014 --zig", (2, 17)),
        ("--locked --zig", (2, 17)),
        ("--manylinux manylinux_2_28 --zig", (2, 28)),
        ("--manylinux=manylinux_2_31 --zig", (2, 31)),
        ("--compatibility linux --zig", None),
        ("--compatibility linux", None),
        ("--compatibility pypi", None),
        ("", None),
    ],
)
def test_wheel_manylinux_glibc_reads_the_compatibility_tag(
    monkeypatch: pytest.MonkeyPatch, arguments: str, tag: tuple[int, int] | None
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setattr(backend.platform, "machine", lambda: "x86_64")
    monkeypatch.setenv("MATURIN_PEP517_ARGS", arguments)

    assert backend._wheel_manylinux_glibc() == tag


def test_bare_zig_default_tag_is_only_assumed_on_x86_64_and_aarch64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A bare `--zig` build on riscv64, where Maturin's zig default
    is manylinux_2_31, not manylinux2014.
    *Do*: Read the wheel's glibc.
    *Assert*: No floor is assumed.
    """
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setattr(backend.platform, "machine", lambda: "riscv64")
    monkeypatch.setenv("MATURIN_PEP517_ARGS", "--locked --zig")

    assert backend._wheel_manylinux_glibc() is None


def test_native_linux_wheel_rust_cli_skips_the_manylinux_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A `--compatibility linux` wheel build, whose vibe-rs needs
    the image's newer glibc.
    *Do*: Stage vibe-rs.
    *Assert*: No manylinux floor applies, so the binary is bundled.
    """
    # Prepare
    binary = b"\x7fELF\0GLIBC_2.39\0"
    backend, _calls, bundled = _stage_portable_rust_cli(
        monkeypatch, tmp_path, binary, built_triple=None
    )
    monkeypatch.setenv("MATURIN_PEP517_ARGS", "--locked --compatibility linux")

    # Do
    backend._stage_rust_cli()

    # Assert
    assert bundled.read_bytes() == binary


@pytest.mark.parametrize("host", ["darwin", "win32"])
def test_zig_flag_off_linux_builds_the_host_rust_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, host: str
) -> None:
    """*Prepare*: A macOS or Windows wheel build with a user-passed `--zig`.
    *Do*: Stage vibe-rs.
    *Assert*: Cargo builds it for the host, not as a Linux ELF via zig.
    """
    # Prepare
    backend, calls, bundled = _stage_portable_rust_cli(
        monkeypatch, tmp_path, b"host-rust-cli", host=host, built_triple=None
    )
    monkeypatch.setenv("MATURIN_PEP517_ARGS", "--locked --zig")

    # Do
    backend._stage_rust_cli()

    # Assert
    [(command, environment)] = calls
    assert "--target" not in command
    assert not any(name.endswith("_LINKER") for name in environment)
    assert bundled.read_bytes() == b"host-rust-cli"


@pytest.mark.parametrize(
    ("binary", "floor"),
    [
        (b"GLIBC_2.2.5\0GLIBC_2.17\0GLIBC_PRIVATE\0", (2, 17)),
        (b"GLIBC_2.9\0GLIBC_2.28\0", (2, 28)),
        (b"no glibc symbols", None),
    ],
)
def test_glibc_floor_is_the_newest_symbol_version(
    monkeypatch: pytest.MonkeyPatch, binary: bytes, floor: tuple[int, int] | None
) -> None:
    backend = _load_backend(monkeypatch)

    assert backend._glibc_floor(binary) == floor


ALSA_PROBE = ["pkg-config", "--exists", "alsa"]
PKGCONF_PROBE = ["pkgconf", "--exists", "alsa"]


def _stage_rust_cli_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    platform: str,
    flags: str,
    probes: dict[str, int],
    build_target: str | None = None,
    pkg_config: str | None = None,
    maturin_args: str | None = None,
) -> list[list[str]]:
    backend = _load_backend(monkeypatch)
    project = tmp_path / "project"
    manifest = project / "vibe/cli-rust/Cargo.toml"
    target = project / ".native-build/vibe-rs-target"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[package]\nname='vibe-rs'\n")
    commands: list[list[str]] = []

    def run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        if command[0] != "cargo":
            if command[0] not in probes:
                raise FileNotFoundError(command[0])
            return subprocess.CompletedProcess(command, probes[command[0]])
        triple = backend._cargo_target_triple(command) or build_target
        built = (target / triple if triple else target) / "release" / backend._TUI_NAME
        built.parent.mkdir(parents=True)
        built.write_bytes(b"rust-cli")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(backend.sys, "platform", platform)
    monkeypatch.setattr(backend, "_PROJECT_ROOT", project)
    monkeypatch.setattr(backend, "_TUI_MANIFEST", manifest)
    monkeypatch.setattr(backend, "_TUI_TARGET", target)
    monkeypatch.setattr(backend, "_BUNDLED_TUI", project / "vibe/_bin/vibe-rs")
    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setenv("CARGO_BUILD_FLAGS", flags)
    if build_target is None:
        monkeypatch.delenv("CARGO_BUILD_TARGET", raising=False)
    else:
        monkeypatch.setenv("CARGO_BUILD_TARGET", build_target)
    if pkg_config is None:
        monkeypatch.delenv("PKG_CONFIG", raising=False)
    else:
        monkeypatch.setenv("PKG_CONFIG", pkg_config)
    if maturin_args is None:
        monkeypatch.delenv("MATURIN_PEP517_ARGS", raising=False)
    else:
        monkeypatch.setenv("MATURIN_PEP517_ARGS", maturin_args)
        monkeypatch.setattr(backend, "_ZIG_WRAPPERS", project / ".native-build/zig")
        monkeypatch.setattr(backend.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(backend.shutil, "which", lambda _name: "/env/maturin")

    backend._stage_rust_cli()

    return commands


def _assert_voice_disabled_warning(capsys: pytest.CaptureFixture[str]) -> None:
    _assert_voice_disabled_warning_text(capsys.readouterr().err)


def _assert_voice_disabled_warning_text(warning: str) -> None:
    assert "without voice mode" in warning
    assert "libasound2-dev" in warning
    assert "alsa-lib-devel" in warning


@pytest.mark.parametrize("flags", ["", "--jobs 2", "--frozen"])
def test_linux_build_without_alsa_disables_voice_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: str,
) -> None:
    commands = _stage_rust_cli_commands(
        monkeypatch, tmp_path, platform="linux", flags=flags, probes={"pkg-config": 1}
    )

    probe, cargo = commands
    assert probe == ALSA_PROBE
    assert cargo[0] == "cargo"
    assert cargo[-1] == "--no-default-features"
    assert cargo[len(cargo) - 1 - len(flags.split()) : -1] == flags.split()
    _assert_voice_disabled_warning(capsys)


def test_linux_build_without_pkg_config_or_pkgconf_disables_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commands = _stage_rust_cli_commands(
        monkeypatch, tmp_path, platform="linux", flags="", probes={}
    )

    *probes, cargo = commands
    assert probes == [ALSA_PROBE, PKGCONF_PROBE]
    assert cargo[-1] == "--no-default-features"
    _assert_voice_disabled_warning(capsys)


def test_linux_build_falls_back_to_pkgconf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commands = _stage_rust_cli_commands(
        monkeypatch, tmp_path, platform="linux", flags="", probes={"pkgconf": 0}
    )

    *probes, cargo = commands
    assert probes == [ALSA_PROBE, PKGCONF_PROBE]
    assert "--no-default-features" not in cargo
    assert capsys.readouterr().err == ""


def test_linux_build_probes_alsa_with_pkg_config_override_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    override = "/opt/cross/bin/pkg-config"
    commands = _stage_rust_cli_commands(
        monkeypatch,
        tmp_path,
        platform="linux",
        flags="",
        probes={override: 1, "pkg-config": 0, "pkgconf": 0},
        pkg_config=override,
    )

    probe, cargo = commands
    assert probe == [override, "--exists", "alsa"]
    assert cargo[-1] == "--no-default-features"
    _assert_voice_disabled_warning(capsys)


def test_linux_build_with_alsa_keeps_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commands = _stage_rust_cli_commands(
        monkeypatch,
        tmp_path,
        platform="linux",
        flags="--jobs 2",
        probes={"pkg-config": 0},
    )

    probe, cargo = commands
    assert probe == ALSA_PROBE
    assert cargo[-2:] == ["--jobs", "2"]
    assert "--no-default-features" not in cargo
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("probes", [{"pkg-config": 0}, {"pkg-config": 1}])
def test_portable_linux_build_disables_voice_and_names_the_opt_ins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    probes: dict[str, int],
) -> None:
    """*Prepare*: A manylinux wheel build from a checkout (zig) on x86_64 Linux,
    with no Cargo features chosen, with and without ALSA development files.
    *Do*: Stage vibe-rs.
    *Assert*: vibe-rs is built without voice for the zig target, because zig
    cannot link the host's libasound for the wheel's older glibc, and the
    warning names the opt-ins: a wheel for this machine only, or Cargo flags.
    """
    # Prepare / Do
    commands = _stage_rust_cli_commands(
        monkeypatch,
        tmp_path,
        platform="linux",
        flags="",
        probes=probes,
        maturin_args="--locked --compatibility manylinux_2_28 --zig",
    )

    # Assert
    [cargo] = commands
    assert cargo[-3:] == [
        "--no-default-features",
        "--target",
        "x86_64-unknown-linux-gnu",
    ]
    warning = capsys.readouterr().err
    assert warning.count("without voice mode") == 1
    assert "glibc 2.28 and later" in warning
    assert 'MATURIN_PEP517_ARGS="--compatibility linux"' in warning
    assert "CARGO_BUILD_FLAGS" in warning
    _assert_voice_disabled_warning_text(warning)


@pytest.mark.parametrize(
    "flags",
    [
        "--features voice",
        "--features=voice",
        "-F voice",
        "-Fvoice",
        "--all-features",
        "--no-default-features",
    ],
)
def test_linux_build_respects_explicit_cargo_features(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: str,
) -> None:
    commands = _stage_rust_cli_commands(
        monkeypatch, tmp_path, platform="linux", flags=flags, probes={"pkg-config": 1}
    )

    [cargo] = commands
    assert cargo[-len(flags.split()) :] == flags.split()
    assert cargo.count("--no-default-features") == flags.count("--no-default-features")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("platform", "flags", "build_target"),
    [
        ("darwin", "", None),
        ("win32", "", None),
        ("linux", "--target x86_64-apple-darwin", None),
        ("linux", "", "x86_64-pc-windows-msvc"),
        ("linux", "--target aarch64-linux-android", None),
        ("linux", "--target aarch64-unknown-linux-gnu", None),
        ("linux", "", "x86_64-unknown-linux-gnu"),
        ("darwin", "--target=aarch64-unknown-linux-gnu", None),
    ],
)
def test_non_native_linux_build_keeps_voice_without_probing_alsa(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    platform: str,
    flags: str,
    build_target: str | None,
) -> None:
    commands = _stage_rust_cli_commands(
        monkeypatch,
        tmp_path,
        platform=platform,
        flags=flags,
        probes={"pkg-config": 1},
        build_target=build_target,
    )

    [cargo] = commands
    assert "--no-default-features" not in cargo
    assert capsys.readouterr().err == ""


def test_stage_rust_cli_skip_preserves_existing_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    bundled = tmp_path / "vibe/_bin" / backend._TUI_NAME
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"editable-rust-cli")
    monkeypatch.setattr(backend, "_BUNDLED_TUI", bundled)
    monkeypatch.setenv("VIBE_SKIP_RUST_TUI", "1")

    backend._stage_rust_cli()

    assert bundled.read_bytes() == b"editable-rust-cli"


def test_stage_rust_cli_failure_preserves_existing_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    project = tmp_path / "project"
    manifest = project / "vibe/cli-rust/Cargo.toml"
    target = project / ".native-build/vibe-rs-target"
    bundled = project / "vibe/_bin" / backend._TUI_NAME
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[package]\nname='vibe-rs'\n")
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"editable-rust-cli")
    monkeypatch.setattr(backend, "_PROJECT_ROOT", project)
    monkeypatch.setattr(backend, "_TUI_MANIFEST", manifest)
    monkeypatch.setattr(backend, "_TUI_TARGET", target)
    monkeypatch.setattr(backend, "_BUNDLED_TUI", bundled)

    def fail_build(*_args: object, **_kwargs: object) -> None:
        raise OSError("compiler failed")

    monkeypatch.setattr(backend.subprocess, "run", fail_build)

    with pytest.raises(OSError, match="compiler failed"):
        backend._stage_rust_cli()

    assert bundled.read_bytes() == b"editable-rust-cli"


@pytest.mark.parametrize("from_sdist", [False, True])
def test_build_wheel_stages_both_native_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, from_sdist: bool
) -> None:
    backend = _load_backend(monkeypatch)
    calls: list[str] = []
    if from_sdist:
        (tmp_path / "PKG-INFO").write_text("")
    monkeypatch.setattr(backend, "_PROJECT_ROOT", tmp_path)

    @contextmanager
    def wheel_harness():
        calls.append("harness")
        yield

    monkeypatch.setattr(backend, "_HARNESS_TARGET", tmp_path / "harness-target")
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setattr(backend, "_stage_rust_cli", lambda: calls.append("tui"))
    monkeypatch.setattr(backend, "_require_cargo", lambda: None)

    def build_wheel(*args: object) -> str:
        calls.append("wheel")
        assert "--locked" in os.environ["MATURIN_PEP517_ARGS"]
        portable = not from_sdist
        assert ("manylinux_2_28" in os.environ["MATURIN_PEP517_ARGS"]) == portable
        assert ("--zig" in os.environ["MATURIN_PEP517_ARGS"]) == portable
        assert os.environ["CARGO_TARGET_DIR"].endswith("harness-target")
        entries = [
            *backend._RUNTIME_WHEEL_MARKERS,
            backend._TUI_WHEEL_ENTRY,
            "mistralai_vibe_local_harness/_native.abi3.so",
        ]
        _write_wheel(tmp_path / "dist" / "mistral_vibe.whl", entries)
        return "mistral_vibe.whl"

    monkeypatch.setattr(backend.maturin, "build_wheel", build_wheel, raising=False)

    assert backend.build_wheel(str(tmp_path / "dist")) == "mistral_vibe.whl"
    assert calls == ["harness", "tui", "wheel"]


@pytest.mark.parametrize(
    "missing_entry",
    [
        "mistralai_vibe_local_harness/protocol.py",
        "mistralai_vibe_local_harness/vibe/__init__.py",
    ],
)
def test_build_wheel_rejects_wheel_missing_harness_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing_entry: str
) -> None:
    backend = _load_backend(monkeypatch)

    @contextmanager
    def wheel_harness():
        yield

    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setattr(backend, "_stage_rust_cli", lambda: None)

    def build_wheel(wheel_directory: str, *_args: object) -> str:
        entries = [
            *backend._RUNTIME_WHEEL_MARKERS,
            backend._TUI_WHEEL_ENTRY,
            "mistralai_vibe_local_harness/_native.abi3.so",
        ]
        entries.remove(missing_entry)
        _write_wheel(Path(wheel_directory) / "wheel.whl", entries)
        return "wheel.whl"

    monkeypatch.setattr(backend.maturin, "build_wheel", build_wheel, raising=False)

    with pytest.raises(backend.NativeBuildError, match="missing bundled components"):
        backend.build_wheel(str(tmp_path))


def test_build_wheel_rejects_wheel_missing_rust_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)

    @contextmanager
    def wheel_harness():
        yield

    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setattr(backend, "_stage_rust_cli", lambda: None)
    monkeypatch.delenv("VIBE_SKIP_RUST_TUI", raising=False)

    def build_wheel(wheel_directory: str, *_args: object) -> str:
        entries = [
            *backend._RUNTIME_WHEEL_MARKERS,
            "mistralai_vibe_local_harness/_native.abi3.so",
        ]
        _write_wheel(Path(wheel_directory) / "wheel.whl", entries)
        return "wheel.whl"

    monkeypatch.setattr(backend.maturin, "build_wheel", build_wheel, raising=False)

    with pytest.raises(backend.NativeBuildError, match="missing bundled components"):
        backend.build_wheel(str(tmp_path))


def test_build_wheel_skip_rust_tui_accepts_wheel_without_rust_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)

    @contextmanager
    def wheel_harness():
        yield

    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setenv("VIBE_SKIP_RUST_TUI", "1")

    def build_wheel(wheel_directory: str, *_args: object) -> str:
        entries = [
            *backend._RUNTIME_WHEEL_MARKERS,
            "mistralai_vibe_local_harness/_native.abi3.so",
        ]
        _write_wheel(Path(wheel_directory) / "wheel.whl", entries)
        return "wheel.whl"

    monkeypatch.setattr(backend.maturin, "build_wheel", build_wheel, raising=False)

    assert backend.build_wheel(str(tmp_path)) == "wheel.whl"


def test_build_wheel_rejects_missing_wheel_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)

    @contextmanager
    def wheel_harness():
        yield

    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setattr(backend, "_stage_rust_cli", lambda: None)
    monkeypatch.setattr(
        backend.maturin, "build_wheel", lambda *_args: "wheel.whl", raising=False
    )

    with pytest.raises(backend.NativeBuildError, match="did not produce"):
        backend.build_wheel(str(tmp_path))


def test_stage_harness_rejects_bundled_runtime_without_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    public = tmp_path / "harness"
    (public / "core").mkdir(parents=True)
    (public / "core/Cargo.toml").write_text("[package]\nname='core'\n")
    (public / "rust-toolchain.toml").write_text("[toolchain]\nchannel='1.97.1'\n")
    bundled_runtime = tmp_path / "mistralai_vibe_local_harness"
    bundled_runtime.mkdir()
    (bundled_runtime / "_native.abi3.so").write_bytes(b"stray-native")

    monkeypatch.setattr(backend, "_PUBLIC_HARNESS_ROOT", public)
    monkeypatch.setattr(backend, "_DASHBOARD_HARNESS_ROOT", tmp_path / "missing")
    monkeypatch.setattr(backend, "_STAGED_CORE", tmp_path / "staged-core")
    monkeypatch.setattr(backend, "_BUNDLED_RUNTIME", bundled_runtime)
    monkeypatch.setattr(backend, "_BUILD_ROOT", tmp_path / "build")

    with pytest.raises(backend.NativeBuildError, match="no complete bundled copy"):
        backend._stage_harness()


_LOCK_PROBE = """
import fcntl
import sys

with open(sys.argv[1], "a+b") as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(1)
"""


@pytest.mark.skipif(os.name == "nt", reason="flock probe is POSIX-only")
def test_build_lock_excludes_other_processes_while_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    build_root = tmp_path / "build"
    monkeypatch.setattr(backend, "_BUILD_ROOT", build_root)
    lock_path = build_root / backend._BUILD_LOCK_NAME
    probe = [sys.executable, "-c", _LOCK_PROBE, str(lock_path)]

    with backend._exclusive_build_lock():
        assert subprocess.run(probe).returncode == 1

    assert subprocess.run(probe).returncode == 0


def test_windows_build_lock_uses_byte_range_locking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.os, "name", "nt")
    monkeypatch.setattr(backend, "_BUILD_ROOT", tmp_path / "build")

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int]] = []

        def locking(self, fd: int, mode: int, nbytes: int) -> None:
            self.calls.append((fd, mode, nbytes))

    fake_msvcrt = FakeMsvcrt()
    monkeypatch.setattr(backend, "msvcrt", fake_msvcrt, raising=False)

    with backend._exclusive_build_lock():
        pass

    assert [call[1:] for call in fake_msvcrt.calls] == [
        (FakeMsvcrt.LK_NBLCK, 1),
        (FakeMsvcrt.LK_UNLCK, 1),
    ]
    assert (tmp_path / "build" / backend._BUILD_LOCK_NAME).read_bytes() == b"\0"


def test_windows_build_lock_retries_only_on_contention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.os, "name", "nt")
    monkeypatch.setattr(backend, "_BUILD_ROOT", tmp_path / "build")
    monkeypatch.setattr(backend.time, "sleep", lambda _seconds: None)

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def __init__(self, failures: list[OSError]) -> None:
            self.failures = failures
            self.attempts = 0

        def locking(self, fd: int, mode: int, nbytes: int) -> None:
            if mode == self.LK_NBLCK:
                self.attempts += 1
                if self.failures:
                    raise self.failures.pop(0)

    contended = FakeMsvcrt([PermissionError(13, "locked"), PermissionError(13, "")])
    monkeypatch.setattr(backend, "msvcrt", contended, raising=False)
    with backend._exclusive_build_lock():
        pass
    assert contended.attempts == 3

    broken = FakeMsvcrt([OSError(9, "bad file descriptor")])
    monkeypatch.setattr(backend, "msvcrt", broken, raising=False)
    with pytest.raises(OSError, match="bad file descriptor"):
        with backend._exclusive_build_lock():
            pass
    assert broken.attempts == 1


def test_windows_build_lock_waits_when_seed_hits_locked_byte(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.os, "name", "nt")
    monkeypatch.setattr(backend, "_BUILD_ROOT", tmp_path / "build")
    monkeypatch.setattr(backend.time, "sleep", lambda _seconds: None)

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def __init__(self) -> None:
            self.attempts = 0

        def locking(self, fd: int, mode: int, nbytes: int) -> None:
            if mode == self.LK_NBLCK:
                self.attempts += 1
                if self.attempts == 1:
                    raise PermissionError(13, "locked")

    def locked_write(fd: int, data: bytes) -> int:
        raise PermissionError(13, "locked")

    fake_msvcrt = FakeMsvcrt()
    monkeypatch.setattr(backend, "msvcrt", fake_msvcrt, raising=False)
    with monkeypatch.context() as patch:
        patch.setattr(backend.os, "write", locked_write)
        with backend._exclusive_build_lock():
            pass

    assert fake_msvcrt.attempts == 2


def test_wheel_staging_restores_editable_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    dashboard = tmp_path / "dashboard-harness"
    source_runtime = dashboard / backend._RUNTIME_PACKAGE
    staged_core = tmp_path / "staged-core"
    bundled_runtime = tmp_path / "mistralai_vibe_local_harness"
    build_root = tmp_path / "build"
    (dashboard / "core/src").mkdir(parents=True)
    (dashboard / "core/Cargo.toml").write_text("[package]\nname='core'\n")
    (dashboard / "core/src/lib.rs").write_text("// core\n")
    (dashboard / "rust-toolchain.toml").write_text("[toolchain]\nchannel='1.97.1'\n")
    source_runtime.mkdir(parents=True)
    (source_runtime / "__init__.py").write_text("SOURCE = 'fresh'\n")
    bundled_runtime.mkdir()
    (bundled_runtime / "__init__.py").write_text("SOURCE = 'editable'\n")
    (bundled_runtime / "_native.abi3.so").write_bytes(b"editable-native")

    monkeypatch.setattr(backend, "_PUBLIC_HARNESS_ROOT", tmp_path / "missing")
    monkeypatch.setattr(backend, "_DASHBOARD_HARNESS_ROOT", dashboard)
    monkeypatch.setattr(backend, "_STAGED_CORE", staged_core)
    monkeypatch.setattr(backend, "_BUNDLED_RUNTIME", bundled_runtime)
    monkeypatch.setattr(backend, "_BUILD_ROOT", build_root)

    with backend._wheel_harness():
        assert (bundled_runtime / "__init__.py").read_text() == "SOURCE = 'fresh'\n"
        assert not (bundled_runtime / "_native.abi3.so").exists()

    assert (bundled_runtime / "__init__.py").read_text() == "SOURCE = 'editable'\n"
    assert (bundled_runtime / "_native.abi3.so").read_bytes() == b"editable-native"


@pytest.mark.parametrize("editable", [False, True])
def test_runtime_copy_failure_preserves_existing_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, editable: bool
) -> None:
    backend = _load_backend(monkeypatch)
    dashboard = tmp_path / "dashboard-harness"
    source_runtime = dashboard / backend._RUNTIME_PACKAGE
    bundled_runtime = tmp_path / "mistralai_vibe_local_harness"
    (dashboard / "core/src").mkdir(parents=True)
    (dashboard / "core/Cargo.toml").write_text("[package]\nname='core'\n")
    (dashboard / "core/src/lib.rs").write_text("// core\n")
    (dashboard / "rust-toolchain.toml").write_text("[toolchain]\nchannel='1.97.1'\n")
    source_runtime.mkdir(parents=True)
    (source_runtime / "__init__.py").write_text("SOURCE = 'fresh'\n")
    bundled_runtime.mkdir()
    (bundled_runtime / "__init__.py").write_text("SOURCE = 'editable'\n")
    (bundled_runtime / "_native.abi3.so").write_bytes(b"editable-native")

    monkeypatch.setattr(backend, "_PUBLIC_HARNESS_ROOT", tmp_path / "missing")
    monkeypatch.setattr(backend, "_DASHBOARD_HARNESS_ROOT", dashboard)
    monkeypatch.setattr(backend, "_STAGED_CORE", tmp_path / "staged-core")
    monkeypatch.setattr(backend, "_BUNDLED_RUNTIME", bundled_runtime)
    monkeypatch.setattr(backend, "_BUILD_ROOT", tmp_path / "build")

    def fail_copy(_source_runtime: Path, _destination: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(backend, "_copy_runtime", fail_copy)

    with pytest.raises(OSError, match="disk full"):
        if editable:
            backend._stage_harness()
        else:
            with backend._wheel_harness():
                raise AssertionError("context body must not run")

    assert (bundled_runtime / "__init__.py").read_text() == "SOURCE = 'editable'\n"
    assert (bundled_runtime / "_native.abi3.so").read_bytes() == b"editable-native"


def test_require_cargo_fails_clearly_when_rust_install_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.shutil, "which", lambda _name: None)
    monkeypatch.setenv("MATURIN_NO_INSTALL_RUST", "1")

    with pytest.raises(backend.NativeBuildError, match="rustup"):
        backend._require_cargo()


def test_require_cargo_lets_maturin_install_rust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.shutil, "which", lambda _name: None)
    monkeypatch.delenv("MATURIN_NO_INSTALL_RUST", raising=False)

    backend._require_cargo()


def test_build_editable_requires_cargo_before_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setattr(backend.shutil, "which", lambda _name: None)
    monkeypatch.setenv("MATURIN_NO_INSTALL_RUST", "1")
    staged: list[str] = []
    monkeypatch.setattr(backend, "_stage_harness", lambda: staged.append("staged"))

    with pytest.raises(backend.NativeBuildError, match="rustup"):
        backend.build_editable("dist")

    assert staged == []


def test_build_sdist_appends_harness_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    harness = tmp_path / "harness"
    runtime = harness / backend._RUNTIME_PACKAGE
    (harness / "core/src").mkdir(parents=True)
    runtime.mkdir(parents=True)
    for path in ["core/Cargo.toml", "core/Cargo.lock", "core/src/lib.rs"]:
        (harness / path).write_text("")
    (harness / "rust-toolchain.toml").write_text("")
    (runtime / "__init__.py").write_text("")
    (runtime / "_native.abi3.so").write_bytes(b"native")
    (runtime / "README.md").write_text("internal")

    def build_sdist(scratch: str, _settings: object) -> str:
        with tarfile.open(Path(scratch) / "mistral_vibe-1.0.tar.gz", "w:gz"):
            pass
        return "mistral_vibe-1.0.tar.gz"

    uv_build = ModuleType("uv_build")
    monkeypatch.setitem(sys.modules, "uv_build", uv_build)
    monkeypatch.setattr(uv_build, "build_sdist", build_sdist, raising=False)
    monkeypatch.setattr(backend, "_PUBLIC_HARNESS_ROOT", harness)

    assert backend.build_sdist(str(tmp_path)) == "mistral_vibe-1.0.tar.gz"
    with tarfile.open(tmp_path / "mistral_vibe-1.0.tar.gz") as sdist:
        names = sdist.getnames()
    runtime_name = f"mistral_vibe-1.0/harness/{backend._RUNTIME_PACKAGE.as_posix()}"
    assert "mistral_vibe-1.0/harness/core/src/lib.rs" in names
    assert "mistral_vibe-1.0/harness/rust-toolchain.toml" in names
    assert f"{runtime_name}/__init__.py" in names
    assert f"{runtime_name}/_native.abi3.so" not in names
    assert f"{runtime_name}/README.md" not in names


def _refuse_harness_staging() -> None:
    raise AssertionError("an external-Harness build must not stage the Harness")


def test_external_harness_editable_install_is_pure_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setenv("VIBE_EXTERNAL_HARNESS", "1")
    uv_build = ModuleType("uv_build")
    monkeypatch.setitem(sys.modules, "uv_build", uv_build)
    calls: list[str] = []

    def prepare_metadata_for_build_editable(*args: object) -> str:
        calls.append("metadata")
        return "mistral_vibe.dist-info"

    def build_editable(*args: object) -> str:
        calls.append("editable")
        return "mistral_vibe-editable.whl"

    monkeypatch.setattr(
        uv_build,
        "prepare_metadata_for_build_editable",
        prepare_metadata_for_build_editable,
        raising=False,
    )
    monkeypatch.setattr(uv_build, "build_editable", build_editable, raising=False)
    monkeypatch.setattr(backend, "_stage_harness", _refuse_harness_staging)

    assert backend.get_requires_for_build_editable() == [backend._UV_BUILD_REQUIREMENT]
    assert (
        backend.prepare_metadata_for_build_editable("meta") == "mistral_vibe.dist-info"
    )
    assert backend.build_editable("dist") == "mistral_vibe-editable.whl"
    assert calls == ["metadata", "editable"]


@pytest.mark.parametrize("value", ["", "0", "true"])
def test_external_harness_needs_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setenv("VIBE_EXTERNAL_HARNESS", value)
    monkeypatch.setattr(
        backend.maturin,
        "get_requires_for_build_editable",
        lambda _settings: ["maturin-runtime"],
        raising=False,
    )

    assert backend.get_requires_for_build_editable() == ["maturin-runtime"]


def test_wheel_build_ignores_external_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setenv("VIBE_EXTERNAL_HARNESS", "1")
    calls: list[str] = []

    @contextmanager
    def wheel_harness():
        calls.append("harness")
        yield

    def build_wheel(wheel_directory: str, *_args: object) -> str:
        calls.append("wheel")
        _write_wheel(
            Path(wheel_directory) / "mistral_vibe.whl",
            [*backend._RUNTIME_WHEEL_MARKERS, backend._TUI_WHEEL_ENTRY],
        )
        return "mistral_vibe.whl"

    monkeypatch.setattr(backend, "_HARNESS_TARGET", tmp_path / "harness-target")
    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setattr(backend, "_stage_rust_cli", lambda: calls.append("tui"))
    monkeypatch.setattr(backend.maturin, "build_wheel", build_wheel, raising=False)

    assert backend.build_wheel(str(tmp_path / "dist")) == "mistral_vibe.whl"
    assert calls == ["harness", "tui", "wheel"]


def _write_file_wheel(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as wheel:
        for name, data in files.items():
            wheel.writestr(name, data)
    return path


def test_prebuilt_natives_assemble_the_combined_layout_without_compiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    harness = _write_file_wheel(
        tmp_path
        / "mistralai_vibe_local_harness-0.5.1-cp312-abi3-manylinux_2_28_x86_64.whl",
        {
            "mistralai_vibe_local_harness/__init__.py": b"",
            "mistralai_vibe_local_harness/_native.abi3.so": b"native",
            "mistralai_vibe_local_harness-0.5.1.dist-info/WHEEL": b"Tag: x",
        },
    )
    rust_tui = tmp_path / "vibe-rs"
    rust_tui.write_bytes(b"tui")
    uv_build = ModuleType("uv_build")
    monkeypatch.setitem(sys.modules, "uv_build", uv_build)

    def build_wheel(directory: str, *args: object) -> str:
        name = "mistral_vibe-1.0.0-py3-none-any.whl"
        _write_file_wheel(
            Path(directory) / name,
            {
                "vibe/__init__.py": b"",
                "vibe/cli-rust/Cargo.toml": b"",
                "mistral_vibe-1.0.0.dist-info/METADATA": b"Name: mistral-vibe",
                "mistral_vibe-1.0.0.dist-info/WHEEL": b"Root-Is-Purelib: true",
                "mistral_vibe-1.0.0.dist-info/RECORD": b"",
            },
        )
        return name

    monkeypatch.setattr(uv_build, "build_wheel", build_wheel, raising=False)
    monkeypatch.setattr(backend, "_require_cargo", _refuse_harness_staging)
    monkeypatch.setenv("VIBE_PREBUILT_HARNESS_WHEEL", str(harness))
    monkeypatch.setenv("VIBE_PREBUILT_RUST_TUI", str(rust_tui))

    assert backend.get_requires_for_build_wheel() == [backend._UV_BUILD_REQUIREMENT]
    name = backend.build_wheel(str(tmp_path))

    assert name == "mistral_vibe-1.0.0-cp312-abi3-manylinux_2_28_x86_64.whl"
    with zipfile.ZipFile(tmp_path / name) as wheel:
        assert sorted(wheel.namelist()) == [
            "mistral_vibe-1.0.0.dist-info/METADATA",
            "mistral_vibe-1.0.0.dist-info/RECORD",
            "mistral_vibe-1.0.0.dist-info/WHEEL",
            "mistralai_vibe_local_harness/__init__.py",
            "mistralai_vibe_local_harness/_native.abi3.so",
            "vibe/__init__.py",
            "vibe/_bin/vibe-rs",
        ]
        assert wheel.getinfo("vibe/_bin/vibe-rs").external_attr >> 16 == 0o100755
        assert wheel.read("mistral_vibe-1.0.0.dist-info/WHEEL").decode().splitlines()[
            2:
        ] == ["Root-Is-Purelib: false", "Tag: cp312-abi3-manylinux_2_28_x86_64"]
        record = wheel.read("mistral_vibe-1.0.0.dist-info/RECORD").decode()
        assert "vibe/_bin/vibe-rs,sha256=" in record


def test_prebuilt_harness_needs_a_prebuilt_rust_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _load_backend(monkeypatch)
    monkeypatch.setenv("VIBE_PREBUILT_HARNESS_WHEEL", "harness.whl")
    monkeypatch.delenv("VIBE_PREBUILT_RUST_TUI", raising=False)

    with pytest.raises(backend.NativeBuildError, match="VIBE_PREBUILT_RUST_TUI"):
        backend.build_wheel("dist")
