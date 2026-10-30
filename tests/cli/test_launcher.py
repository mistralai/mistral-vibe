from __future__ import annotations

from pathlib import Path
import shlex
import subprocess

import pytest

import vibe.cli.launcher as launcher


def _capture_execvpe(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace os.execvpe (which never returns) with a recorder."""
    calls: list[dict[str, object]] = []

    def _record(file: str, argv: list[str], env: dict[str, str]) -> None:
        calls.append({"file": file, "argv": argv, "env": env})

    monkeypatch.setattr(launcher.os, "execvpe", _record)
    return calls


def _forbid_execvpe(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("os.execvpe called on the Windows branch")

    monkeypatch.setattr(launcher.os, "execvpe", _fail)


def test_bundled_binary_execs_with_app_server_bin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = tmp_path / "_bin" / "vibe-rs"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    console_script = tmp_path / "venv" / "bin" / "vibe"
    console_script.parent.mkdir(parents=True)

    monkeypatch.setattr(launcher, "_BUNDLED_BIN", bundled)
    monkeypatch.setattr("sys.argv", [str(console_script)])
    monkeypatch.setattr("sys.platform", "linux")
    # Present in the parent env; the bundled path must drop both.
    monkeypatch.setenv("VIBE_APP_SERVER_CWD", "/stale")
    monkeypatch.setenv("VIBE_APP_SERVER_CMD", "uv run vibe-app-server")
    calls = _capture_execvpe(monkeypatch)

    launcher.exec_rust_cli(["--version"])

    assert len(calls) == 1
    (call,) = calls
    assert call["file"] == str(bundled)
    assert call["argv"] == [str(bundled), "--version"]
    env = call["env"]
    assert isinstance(env, dict)
    assert env["VIBE_APP_SERVER_BIN"] == str(
        console_script.resolve().parent / launcher._APP_SERVER_SCRIPT
    )
    assert "VIBE_APP_SERVER_CWD" not in env
    assert "VIBE_APP_SERVER_CMD" not in env


def test_app_server_cmd_runs_the_bundled_binary_against_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = tmp_path / "_internal" / "vibe" / "_bin" / "vibe-rs"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    app_server_cmd = [str(tmp_path / "dir with space" / "vibe-acp"), "--serve"]

    monkeypatch.setattr(launcher, "_BUNDLED_BIN", bundled)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("VIBE_APP_SERVER_BIN", "/stale/vibe-app-server")
    monkeypatch.setenv("VIBE_APP_SERVER_CWD", "/stale")
    calls = _capture_execvpe(monkeypatch)

    launcher.exec_rust_cli(
        ["--setup"], extra_env={"EXTRA": "1"}, app_server_cmd=app_server_cmd
    )

    (call,) = calls
    assert call["argv"] == [str(bundled), "--setup"]
    env = call["env"]
    assert isinstance(env, dict)
    assert shlex.split(env["VIBE_APP_SERVER_CMD"]) == app_server_cmd
    assert env["EXTRA"] == "1"
    assert "VIBE_APP_SERVER_BIN" not in env
    assert "VIBE_APP_SERVER_CWD" not in env


def test_app_server_cmd_without_bundled_rust_cli_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "_bin" / "vibe-rs"
    monkeypatch.setattr(launcher, "_BUNDLED_BIN", missing)
    monkeypatch.setattr(launcher, "_MANIFEST", tmp_path / "cli-rust" / "Cargo.toml")
    _forbid_execvpe(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        launcher.exec_rust_cli(["--setup"], app_server_cmd=["vibe-acp"])

    assert str(missing) in str(excinfo.value)


def test_source_checkout_execs_release_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "cli-rust" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.touch()
    release_bin = tmp_path / "cli-rust" / "target" / "release" / "vibe-rs"
    release_bin.parent.mkdir(parents=True)
    release_bin.touch()
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setattr(launcher, "_BUNDLED_BIN", tmp_path / "_bin" / "missing")
    monkeypatch.setattr(launcher, "_MANIFEST", manifest)
    monkeypatch.setattr(launcher, "_RELEASE_BIN", release_bin)
    monkeypatch.setattr(launcher, "_PROJECT_ROOT", project_root)
    monkeypatch.setattr("sys.platform", "linux")
    # Present in the parent env; the source path must drop it.
    monkeypatch.setenv("VIBE_APP_SERVER_BIN", "/stale/vibe-app-server")
    # The Makefile owns this on the source path; it must survive so `make run` works.
    monkeypatch.setenv("VIBE_APP_SERVER_CMD", "uv run vibe-app-server")
    calls = _capture_execvpe(monkeypatch)

    launcher.exec_rust_cli(["some", "arg"])

    (call,) = calls
    assert call["file"] == str(release_bin)
    assert call["argv"] == [str(release_bin), "some", "arg"]
    env = call["env"]
    assert isinstance(env, dict)
    assert env["VIBE_APP_SERVER_CWD"] == str(project_root)
    assert "VIBE_APP_SERVER_BIN" not in env
    assert env["VIBE_APP_SERVER_CMD"] == "uv run vibe-app-server"


def test_source_checkout_builds_when_release_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = tmp_path / "cli-rust" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.touch()
    release_bin = tmp_path / "cli-rust" / "target" / "release" / "vibe-rs"
    release_bin.parent.mkdir(parents=True)  # binary intentionally absent

    monkeypatch.setattr(launcher, "_BUNDLED_BIN", tmp_path / "_bin" / "missing")
    monkeypatch.setattr(launcher, "_MANIFEST", manifest)
    monkeypatch.setattr(launcher, "_RELEASE_BIN", release_bin)
    monkeypatch.setattr(launcher, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("sys.platform", "linux")

    built: list[bool] = []

    def _fake_build() -> None:
        built.append(True)
        release_bin.touch()

    monkeypatch.setattr(launcher, "_build_release", _fake_build)
    calls = _capture_execvpe(monkeypatch)

    launcher.exec_rust_cli([])

    assert built == [True]
    (call,) = calls
    assert call["file"] == str(release_bin)


def test_build_release_forwards_cargo_build_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release_bin = tmp_path / "vibe-rs"
    monkeypatch.setattr(launcher, "_MANIFEST", tmp_path / "Cargo.toml")
    monkeypatch.setattr(launcher, "_RELEASE_BIN", release_bin)
    monkeypatch.setattr(launcher, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("CARGO_BUILD_FLAGS", "--no-default-features")

    recorded: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        recorded.append(cmd)
        release_bin.touch()
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    launcher._build_release()

    assert recorded
    assert "--no-default-features" in recorded[0]


def test_missing_bundle_and_source_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_manifest = tmp_path / "cli-rust" / "Cargo.toml"

    monkeypatch.setattr(launcher, "_BUNDLED_BIN", tmp_path / "_bin" / "missing")
    monkeypatch.setattr(launcher, "_MANIFEST", missing_manifest)
    _forbid_execvpe(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        launcher.exec_rust_cli([])

    assert str(missing_manifest) in str(excinfo.value)


def test_windows_uses_subprocess_and_forwards_returncode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = tmp_path / "_bin" / "vibe-rs.exe"
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    console_script = tmp_path / "venv" / "Scripts" / "vibe.exe"
    console_script.parent.mkdir(parents=True)

    monkeypatch.setattr(launcher, "_BUNDLED_BIN", bundled)
    monkeypatch.setattr("sys.argv", [str(console_script)])
    monkeypatch.setattr("sys.platform", "win32")
    _forbid_execvpe(monkeypatch)

    recorded: list[list[str]] = []

    def _fake_run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        recorded.append(argv)
        return subprocess.CompletedProcess(argv, returncode=7)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        launcher.exec_rust_cli(["--version"])

    assert excinfo.value.code == 7
    assert recorded == [[str(bundled), "--version"]]


@pytest.mark.parametrize(
    "args", [[], ["update"], ["--check-upgrade"], ["--", "update"], ["mcp", "list"]]
)
def test_main_passes_argv_to_rust_untouched(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", *args])
    called_with: list[list[str]] = []
    monkeypatch.setattr(launcher, "exec_rust_cli", called_with.append)

    launcher.main()

    assert called_with == [args]
