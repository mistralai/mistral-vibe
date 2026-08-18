from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts import check_startup_import_cost as mod


def test_parse_importtime_counts_modules_and_sorts_by_self_time() -> None:
    stderr = (
        "import time: self [us] | cumulative | imported package\n"
        "import time:       100 |        500 | package_a\n"
        "import time:       300 |        400 | package_b\n"
        "import time:       200 |        600 | package_c\n"
    )

    count, modules = mod._parse_importtime(stderr)

    assert count == 3
    assert modules == [(300, "package_b"), (200, "package_c"), (100, "package_a")]


def test_parse_importtime_skips_header_and_malformed_lines() -> None:
    stderr = (
        "import time: self [us] | cumulative | imported package\n"
        "import time: not-a-number | 500 | bad_line\n"
        "garbage line\n"
        "import time:       42 |        99 | good_module\n"
        "import time:       10 |   20 |   30 | extra_field\n"
    )

    count, modules = mod._parse_importtime(stderr)

    assert count == 1
    assert modules == [(42, "good_module")]


def test_parse_importtime_returns_empty_on_no_matches() -> None:
    count, modules = mod._parse_importtime("nothing useful here\n")

    assert count == 0
    assert modules == []


def test_strace_total_calls_extracts_total() -> None:
    summary = (
        "% time     seconds  usecs/call     calls    errors syscall\n"
        "------ ----------- ----------- --------- --------- ----------------\n"
        " 50.00    0.000050           1        50           openat\n"
        " 50.00    0.000050           1        50           close\n"
        "100.00    0.000100           1       100           total\n"
    )

    assert mod._strace_total_calls(summary) == 100


def test_strace_total_calls_extracts_total_with_errors_column() -> None:
    summary = (
        "% time     seconds  usecs/call     calls    errors syscall\n"
        "------ ----------- ----------- --------- --------- ----------------\n"
        " 50.00    0.000050           1        50           openat\n"
        " 50.00    0.000050           1        50           close\n"
        "100.00    0.000100           1       100         5   total\n"
    )

    assert mod._strace_total_calls(summary) == 100


def test_strace_total_calls_returns_none_without_total_line() -> None:
    summary = "% time     seconds  usecs/call     calls    errors syscall\n"

    assert mod._strace_total_calls(summary) is None


def test_load_config_parses_commands_with_budgets(tmp_path: Path) -> None:
    content = (
        'strace_code = "import vibe"\n'
        "slowest_count = 5\n"
        "\n"
        "[[commands]]\n"
        'label = "import vibe"\n'
        'code = "import vibe"\n'
        "budget = 68\n"
        "\n"
        "[[commands]]\n"
        'label = "VibeApp"\n'
        'code = "from vibe.cli.textual_ui.app import VibeApp"\n'
    )

    config = mod._load_config(_write_config(tmp_path, content))

    assert config.slowest_count == 5
    assert config.strace_code == "import vibe"
    assert len(config.commands) == 2
    assert config.commands[0].label == "import vibe"
    assert config.commands[0].budget == 68
    assert config.commands[1].budget is None


def test_load_config_defaults_slowest_count_to_15(tmp_path: Path) -> None:
    content = '[[commands]]\nlabel = "x"\ncode = "import x"\n'

    config = mod._load_config(_write_config(tmp_path, content))

    assert config.slowest_count == 15


def test_load_config_rejects_empty_commands(tmp_path: Path) -> None:
    content = "slowest_count = 5\n"

    with pytest.raises(SystemExit, match="commands"):
        mod._load_config(_write_config(tmp_path, content))


def test_load_config_rejects_command_missing_label(tmp_path: Path) -> None:
    content = '[[commands]]\ncode = "import x"\n'

    with pytest.raises(SystemExit, match="label"):
        mod._load_config(_write_config(tmp_path, content))


def test_load_config_coerces_non_int_budget_to_none(tmp_path: Path) -> None:
    content = '[[commands]]\nlabel = "x"\ncode = "import x"\nbudget = "not-a-number"\n'

    config = mod._load_config(_write_config(tmp_path, content))

    assert config.commands[0].budget is None


def test_find_wheel_returns_single_wheel(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    wheel = dist_dir / "mistral_vibe-1.0.0-py3-none-any.whl"
    wheel.write_text("")

    assert mod._find_wheel(dist_dir) == wheel


def test_find_wheel_raises_on_no_wheels(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    with pytest.raises(SystemExit, match="no wheel"):
        mod._find_wheel(dist_dir)


def test_find_wheel_raises_on_multiple_wheels(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "mistral_vibe-1.0.0-py3-none-any.whl").write_text("")
    (dist_dir / "mistral_vibe-1.0.0-py3-none-manylinux.whl").write_text("")

    with pytest.raises(SystemExit, match="expected one wheel"):
        mod._find_wheel(dist_dir)


def test_measure_strace_skips_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/bin/strace")
    failed = subprocess.CompletedProcess(
        args=["strace"],
        returncode=1,
        stdout="",
        stderr="ptrace: Operation not permitted",
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("check") is not True, (
            "strace must not use check=True (best-effort)"
        )
        return failed

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    mod._measure_strace(Path("/fake/python"), "import vibe")

    captured = capsys.readouterr()
    assert "strace failed (exit 1) - skipped" in captured.out
    assert "ptrace: Operation not permitted" in captured.out


def test_measure_strace_skips_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    mod._measure_strace(Path("/fake/python"), "import vibe")

    captured = capsys.readouterr()
    assert "strace unavailable - skipped" in captured.out


def test_measurement_env_strips_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/some/source/tree")
    monkeypatch.setenv("KEEP_ME", "yes")

    env = mod._measurement_env()

    assert "PYTHONPATH" not in env
    assert env["KEEP_ME"] == "yes"


def test_measure_importtime_prints_stderr_and_exits_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = subprocess.CompletedProcess(
        args=["python"],
        returncode=1,
        stdout="",
        stderr="Traceback (most recent call last):\n  ImportError: bad wheel\n",
    )
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: failed)

    with pytest.raises(SystemExit, match="import failed"):
        mod._measure_importtime(Path("/fake/python"), "import vibe")

    captured = capsys.readouterr()
    assert "ImportError: bad wheel" in captured.err


def _write_config(tmp_path: Path, content: str) -> Path:
    target = tmp_path / "config.toml"
    target.write_text(content, encoding="utf-8")
    return target
