from __future__ import annotations

import pytest

import vibe.cli.entrypoint
import vibe.cli.launcher as launcher


def test_pty_helper_flag_ignores_inherited_rust_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Rust client and app server export VIBE_CLI=rust; the managed shell
    # spawns the PTY helper as an inheriting subprocess. The helper flag must
    # reach the Python entrypoint, not launch Rust.
    monkeypatch.setenv("VIBE_CLI", "rust")
    monkeypatch.setattr(
        "sys.argv", ["vibe", "--internal-posix-pty-helper", "3", "4", "5"]
    )

    def _fail_rust(_passthrough: list[str]) -> None:
        raise AssertionError("Rust CLI launched for the PTY helper argument")

    reached_entrypoint = False

    def _entrypoint() -> None:
        nonlocal reached_entrypoint
        reached_entrypoint = True

    monkeypatch.setattr("vibe.cli._rust.exec_rust_cli", _fail_rust)
    monkeypatch.setattr("vibe.cli.entrypoint.main", _entrypoint)

    launcher.main()

    assert reached_entrypoint


@pytest.mark.parametrize(
    "args",
    [
        ["update"],
        ["--check-upgrade"],
        ["update", "--workdir", "."],
        ["--workdir", ".", "--check-upgrade"],
        ["--check-upgrade", "--", "prompt"],
    ],
)
def test_upgrade_commands_use_python_with_rust_selected(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setenv("VIBE_CLI", "rust")
    monkeypatch.setattr("sys.argv", ["vibe", *args])

    def _fail_rust(_passthrough: list[str]) -> None:
        raise AssertionError("Rust CLI launched for an upgrade command")

    def _entrypoint() -> None:
        assert vibe.cli.entrypoint.parse_arguments().check_upgrade
        raise SystemExit(0)

    monkeypatch.setattr("vibe.cli._rust.exec_rust_cli", _fail_rust)
    monkeypatch.setattr("vibe.cli.entrypoint.main", _entrypoint)

    with pytest.raises(SystemExit) as excinfo:
        launcher.main()

    assert excinfo.value.code == 0


@pytest.mark.parametrize(
    "args",
    [
        ["some", "args"],
        ["--", "update"],
        ["--", "--check-upgrade"],
        ["--prompt", "update"],
        ["--prompt=--check-upgrade"],
    ],
)
def test_rust_selector_launches_rust_for_non_python_commands(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setenv("VIBE_CLI", "rust")
    monkeypatch.setattr("sys.argv", ["vibe", *args])

    called_with: list[list[str]] = []

    def _record_rust(passthrough: list[str]) -> None:
        called_with.append(passthrough)
        raise SystemExit(0)  # os.execvpe never returns in production

    monkeypatch.setattr("vibe.cli._rust.exec_rust_cli", _record_rust)

    with pytest.raises(SystemExit):
        launcher.main()

    assert called_with == [args]
