from __future__ import annotations

from importlib import metadata
import importlib.util
from pathlib import Path
import types

import pytest

from tests import TESTS_ROOT

HELPER_PATH = TESTS_ROOT.parent / "pyinstaller" / "rust_tui.py"


def load_helper() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("pyinstaller_rust_tui", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_SKIP_RUST_TUI", raising=False)
    monkeypatch.delenv("VIBE_ACP_RUST_TUI", raising=False)


def _fake_tui(tmp_path: Path) -> Path:
    binary = tmp_path / "vibe-rs"
    binary.touch()
    return binary


def test_skip_env_collects_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = load_helper()
    monkeypatch.setenv("VIBE_SKIP_RUST_TUI", "1")
    monkeypatch.setattr(helper, "_installed_tui", lambda: None)

    assert helper.collect_rust_tui() == []


def test_override_env_bundles_the_given_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = load_helper()
    binary = _fake_tui(tmp_path)
    monkeypatch.setenv("VIBE_ACP_RUST_TUI", str(binary))
    monkeypatch.setattr(helper, "_installed_tui", lambda: None)

    assert helper.collect_rust_tui() == [(str(binary), "vibe/_bin")]


def test_installed_wheel_binary_is_bundled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = load_helper()
    binary = _fake_tui(tmp_path)
    monkeypatch.setattr(helper, "_installed_tui", lambda: binary)

    assert helper.collect_rust_tui() == [(str(binary), "vibe/_bin")]


class _FakeDistribution:
    def __init__(self, root: Path) -> None:
        self._root = root

    def locate_file(self, path: object) -> Path:
        return self._root / str(path)


def _record(root: Path, *entries: str) -> list[metadata.PackagePath]:
    files = []
    for entry in entries:
        path = metadata.PackagePath(entry)
        path.dist = _FakeDistribution(root)  # type: ignore[assignment]
        files.append(path)
    return files


def test_installed_tui_uses_the_wheel_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = load_helper()
    entry = f"vibe/_bin/{helper._TUI_NAME}"
    monkeypatch.setattr(
        helper.metadata, "files", lambda _name: _record(tmp_path, "vibe/x.py", entry)
    )

    assert helper._installed_tui() == tmp_path / entry


def test_installed_tui_ignores_an_editable_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = load_helper()
    monkeypatch.setattr(
        helper.metadata, "files", lambda _name: _record(tmp_path, "_vibe.pth")
    )

    assert helper._installed_tui() is None


def test_installed_tui_without_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = load_helper()

    def _missing(name: str) -> None:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(helper.metadata, "files", _missing)

    assert helper._installed_tui() is None


def test_missing_binary_fails_the_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper = load_helper()
    monkeypatch.setattr(helper, "_installed_tui", lambda: tmp_path / "vibe-rs")

    with pytest.raises(helper.RustTuiRequiredError, match="VIBE_SKIP_RUST_TUI"):
        helper.collect_rust_tui()
