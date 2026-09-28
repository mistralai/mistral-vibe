from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest

BACKEND_PATH = Path(__file__).parents[1] / "build_backend/maturin_backend.py"


def _load_backend(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    maturin = ModuleType("maturin")
    monkeypatch.setitem(sys.modules, "maturin", maturin)
    spec = importlib.util.spec_from_file_location(
        "vibe_combined_maturin_backend", BACKEND_PATH
    )
    assert spec is not None and spec.loader is not None
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


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


def test_build_wheel_stages_both_native_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = _load_backend(monkeypatch)
    calls: list[str] = []

    @contextmanager
    def wheel_harness():
        calls.append("harness")
        yield

    monkeypatch.setattr(backend, "_HARNESS_TARGET", tmp_path / "harness-target")
    monkeypatch.setattr(backend.sys, "platform", "linux")
    monkeypatch.setattr(backend, "_wheel_harness", wheel_harness)
    monkeypatch.setattr(backend, "_stage_rust_cli", lambda: calls.append("tui"))

    def build_wheel(*args: object) -> str:
        calls.append("wheel")
        assert "--locked" in os.environ["MATURIN_PEP517_ARGS"]
        assert "--compatibility manylinux_2_28" in os.environ["MATURIN_PEP517_ARGS"]
        assert "--zig" in os.environ["MATURIN_PEP517_ARGS"]
        assert os.environ["CARGO_TARGET_DIR"].endswith("harness-target")
        return "mistral_vibe.whl"

    monkeypatch.setattr(backend.maturin, "build_wheel", build_wheel, raising=False)

    assert backend.build_wheel("dist") == "mistral_vibe.whl"
    assert calls == ["harness", "tui", "wheel"]


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


def test_source_distribution_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _load_backend(monkeypatch)

    with pytest.raises(backend.NativeBuildError, match="platform wheels only"):
        backend.build_sdist("dist")
