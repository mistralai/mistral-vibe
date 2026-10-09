from __future__ import annotations

import json
from pathlib import Path
import time

import pytest

import vibe.cli._rust as rust
import vibe.cli.launcher as launcher
from vibe.core.experiments._constants import EVAL_CACHE_TTL_SECONDS
from vibe.core.experiments.active import ExperimentName
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE


def test_pty_helper_flag_ignores_inherited_rust_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Import the entrypoint with the real argv first: importing it after the
    # argv patch below would run its module-top pty-helper check for real.
    import vibe.cli.entrypoint as entrypoint

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
    monkeypatch.setattr(entrypoint, "main", _entrypoint)

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
        ["--", "update"],
        ["--", "--check-upgrade"],
        ["--prompt", "update"],
        ["--prompt=--check-upgrade"],
    ],
)
def test_upgrade_commands_launch_rust_with_rust_selected(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    # `vibe update` / `--check-upgrade` now run in the Rust client; the
    # launcher only rewrites its own process, so the passthrough args reach
    # the Rust binary untouched (a leading bare `update` is rewritten there).
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


def _write_eval_cache(*entries: tuple[str, int, str]) -> None:
    EXPERIMENT_EVAL_CACHE_FILE.path.write_text(
        json.dumps({
            key: {
                "stored_at_timestamp": stored_at,
                "payload": {
                    "features": {
                        ExperimentName.RUST_TUI_ROLLOUT.value: {
                            "defaultValue": "python",
                            "rules": [{"force": variant, "tracks": []}],
                        }
                    }
                },
            }
            for key, stored_at, variant in entries
        }),
        encoding="utf-8",
    )


@pytest.fixture
def rollout_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    bundled = tmp_path / "vibe-rs"
    bundled.touch()
    monkeypatch.setattr(rust, "_BUNDLED_BIN", bundled)
    monkeypatch.delenv("VIBE_CLI", raising=False)
    monkeypatch.delenv("VIBE_HOME", raising=False)
    launched: list[str] = []

    def _record_rust(_passthrough: list[str], *, rollout: bool = False) -> None:
        launched.append("rust" if rollout else "rust-selector")
        raise SystemExit(0)

    def _record_python() -> None:
        launched.append("python")

    monkeypatch.setattr("vibe.cli._rust.exec_rust_cli", _record_rust)
    monkeypatch.setattr("vibe.cli.entrypoint.main", _record_python)
    return launched


def _launch(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", *args])
    try:
        launcher.main()
    except SystemExit:
        pass


def test_rollout_rust_variant_launches_rust(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str]
) -> None:
    _write_eval_cache(("key", int(time.time()), "rust"))

    _launch(monkeypatch, "-c")

    assert rollout_env == ["rust"]


@pytest.mark.parametrize("variant", ["python", "unknown"])
def test_rollout_non_rust_variant_launches_python(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str], variant: str
) -> None:
    _write_eval_cache(("key", int(time.time()), variant))

    _launch(monkeypatch)

    assert rollout_env == ["python"]


@pytest.mark.parametrize("content", [None, "not json", "[]"])
def test_rollout_missing_or_invalid_cache_launches_python(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str], content: str | None
) -> None:
    if content is not None:
        EXPERIMENT_EVAL_CACHE_FILE.path.write_text(content, encoding="utf-8")

    _launch(monkeypatch)

    assert rollout_env == ["python"]


def test_rollout_ignores_expired_entry(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str]
) -> None:
    _write_eval_cache(("key", int(time.time()) - EVAL_CACHE_TTL_SECONDS - 1, "rust"))

    _launch(monkeypatch)

    assert rollout_env == ["python"]


@pytest.mark.parametrize(
    ("older", "newer", "expected"),
    [("rust", "python", "python"), ("python", "rust", "rust")],
)
def test_rollout_uses_most_recent_entry(
    monkeypatch: pytest.MonkeyPatch,
    rollout_env: list[str],
    older: str,
    newer: str,
    expected: str,
) -> None:
    now = int(time.time())
    _write_eval_cache(("newer", now, newer), ("older", now - 60, older))

    _launch(monkeypatch)

    assert rollout_env == [expected]


@pytest.mark.parametrize(
    "args",
    [
        ["--setup"],
        ["--legacy-harness"],
        ["--experimental-harness", "prompt"],
        ["-p", "prompt", "--agent-socket", "/tmp/agent.sock"],
        ["-p", "prompt", "--agent-socket=/tmp/agent.sock"],
    ],
)
def test_rollout_keeps_python_for_flags_rust_rejects(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str], args: list[str]
) -> None:
    _write_eval_cache(("key", int(time.time()), "rust"))

    _launch(monkeypatch, *args)

    assert rollout_env == ["python"]


def test_python_selector_overrides_rust_rollout(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str]
) -> None:
    _write_eval_cache(("key", int(time.time()), "rust"))
    monkeypatch.setenv("VIBE_CLI", "python")

    _launch(monkeypatch)

    assert rollout_env == ["python"]


def test_rollout_without_bundled_binary_launches_python(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str], tmp_path: Path
) -> None:
    _write_eval_cache(("key", int(time.time()), "rust"))
    monkeypatch.setattr(rust, "_BUNDLED_BIN", tmp_path / "missing")

    _launch(monkeypatch)

    assert rollout_env == ["python"]


def test_rust_selector_is_not_a_rollout_launch(
    monkeypatch: pytest.MonkeyPatch, rollout_env: list[str]
) -> None:
    _write_eval_cache(("key", int(time.time()), "rust"))
    monkeypatch.setenv("VIBE_CLI", "rust")

    _launch(monkeypatch)

    assert rollout_env == ["rust-selector"]


def test_rollout_key_matches_registered_experiment() -> None:
    assert rust.ROLLOUT_EXPERIMENT_KEY == ExperimentName.RUST_TUI_ROLLOUT.value
