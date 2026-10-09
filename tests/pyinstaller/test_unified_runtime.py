"""Build-time behavior of the PyInstaller Unified Runtime helper.

The helper backs all three spec files (vibe.spec, vibe-acp.spec,
vibe-app-server.spec): the Runtime must be collected unconditionally and a
missing Runtime must abort the packaging build instead of producing a
legacy-only executable.
"""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from tests import TESTS_ROOT

HELPER_PATH = TESTS_ROOT.parent / "pyinstaller" / "unified_runtime.py"


def load_helper() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pyinstaller_unified_runtime", HELPER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeFinder:
    """Stands in for importlib.util with a fixed set of findable modules."""

    def __init__(self, *modules: str) -> None:
        self.modules = set(modules)

    def find_spec(self, name: str) -> object | None:
        return object() if name in self.modules else None


class FakeRuntimeHost:
    """The smallest Host contract the build-time probe checks."""

    def configure_hook_handlers(self) -> None:
        pass


def fake_import_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.create_harness_host = FakeRuntimeHost  # pyright: ignore[reportAttributeAccessIssue]
    return module


def install_fake_collect_all(
    monkeypatch: pytest.MonkeyPatch,
    collected_by_package: dict[str, tuple[list[object], list[object], list[object]]],
) -> list[str]:
    collected_packages: list[str] = []

    def fake_collect_all(package_name: str, **_kwargs: object):
        collected_packages.append(package_name)
        return collected_by_package[package_name]

    fake_hooks = types.ModuleType("PyInstaller.utils.hooks")
    fake_hooks.collect_all = fake_collect_all  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "PyInstaller", types.ModuleType("PyInstaller"))
    monkeypatch.setitem(
        sys.modules, "PyInstaller.utils", types.ModuleType("PyInstaller.utils")
    )
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", fake_hooks)
    return collected_packages


def install_findable_runtime(
    monkeypatch: pytest.MonkeyPatch, helper: types.ModuleType
) -> None:
    monkeypatch.setattr(
        helper,
        "util",
        FakeFinder(
            "mistralai_vibe_local_harness", "mistralai_vibe_local_harness._native"
        ),
    )
    monkeypatch.setattr(helper, "import_module", fake_import_module)


def test_helper_exists_at_the_spec_location() -> None:
    assert HELPER_PATH.is_file()


def test_missing_runtime_package_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(helper, "util", FakeFinder())

    with pytest.raises(helper.UnifiedRuntimeRequiredError, match="not installed"):
        helper.require_unified_runtime()


def test_missing_native_extension_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(helper, "util", FakeFinder("mistralai_vibe_local_harness"))

    with pytest.raises(
        helper.UnifiedRuntimeRequiredError, match=r"_native' native extension"
    ):
        helper.require_unified_runtime()


def test_runtime_import_failure_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "util",
        FakeFinder(
            "mistralai_vibe_local_harness", "mistralai_vibe_local_harness._native"
        ),
    )

    def broken_import_module(name: str) -> types.ModuleType:
        raise ImportError(f"cannot import {name}")

    monkeypatch.setattr(helper, "import_module", broken_import_module)

    with pytest.raises(helper.UnifiedRuntimeRequiredError, match="ImportError"):
        helper.require_unified_runtime()


def test_runtime_without_host_factory_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "util",
        FakeFinder(
            "mistralai_vibe_local_harness", "mistralai_vibe_local_harness._native"
        ),
    )
    monkeypatch.setattr(helper, "import_module", lambda name: types.ModuleType(name))

    with pytest.raises(helper.UnifiedRuntimeRequiredError, match="create_harness_host"):
        helper.require_unified_runtime()


def test_collect_unified_runtime_collects_the_runtime_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    install_findable_runtime(monkeypatch, helper)
    sentinel = ([object()], [object()], [object()])
    collected_packages = install_fake_collect_all(
        monkeypatch, {"mistralai_vibe_local_harness": sentinel}
    )

    result = helper.collect_unified_runtime(platform="linux")

    assert collected_packages == ["mistralai_vibe_local_harness"]
    assert result is sentinel


def test_collect_unified_runtime_also_collects_winpty_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    install_findable_runtime(monkeypatch, helper)
    collected_packages = install_fake_collect_all(
        monkeypatch,
        {
            "mistralai_vibe_local_harness": (["rt-data"], ["rt-bin"], ["rt-mod"]),
            "winpty": (["pty-data"], ["pty-bin"], ["pty-mod"]),
        },
    )

    result = helper.collect_unified_runtime(platform="win32")

    assert collected_packages == ["mistralai_vibe_local_harness", "winpty"]
    assert result == (
        ["rt-data", "pty-data"],
        ["rt-bin", "pty-bin"],
        ["rt-mod", "pty-mod"],
    )


def test_missing_winpty_fails_the_windows_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    install_findable_runtime(monkeypatch, helper)

    def import_without_winpty(name: str) -> types.ModuleType:
        if name == "winpty":
            raise ModuleNotFoundError("No module named 'winpty'")
        return fake_import_module(name)

    monkeypatch.setattr(helper, "import_module", import_without_winpty)
    collected_packages = install_fake_collect_all(monkeypatch, {})

    with pytest.raises(helper.UnifiedRuntimeRequiredError, match="pywinpty"):
        helper.collect_unified_runtime(platform="win32")
    assert collected_packages == []


def test_missing_runtime_package_fails_the_build_message_mentions_reinstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(helper, "util", FakeFinder())

    with pytest.raises(helper.UnifiedRuntimeRequiredError, match="uv sync"):
        helper.require_unified_runtime()


def test_host_construction_failure_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "util",
        FakeFinder(
            "mistralai_vibe_local_harness", "mistralai_vibe_local_harness._native"
        ),
    )

    def broken_factory_import(name: str) -> types.ModuleType:
        module = types.ModuleType(name)

        def broken_factory() -> None:
            raise RuntimeError("host construction failed")

        module.create_harness_host = broken_factory  # pyright: ignore[reportAttributeAccessIssue]
        return module

    monkeypatch.setattr(helper, "import_module", broken_factory_import)

    with pytest.raises(
        helper.UnifiedRuntimeRequiredError,
        match="constructing the Unified Runtime Host",
    ):
        helper.require_unified_runtime()


def test_host_without_hook_handler_configuration_fails_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(
        helper,
        "util",
        FakeFinder(
            "mistralai_vibe_local_harness", "mistralai_vibe_local_harness._native"
        ),
    )

    def incompatible_import(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.create_harness_host = object  # pyright: ignore[reportAttributeAccessIssue]
        return module

    monkeypatch.setattr(helper, "import_module", incompatible_import)

    with pytest.raises(
        helper.UnifiedRuntimeRequiredError, match="configure_hook_handlers"
    ):
        helper.require_unified_runtime()


def test_collect_unified_runtime_runs_the_guard_before_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = load_helper()
    monkeypatch.setattr(helper, "util", FakeFinder())

    with pytest.raises(helper.UnifiedRuntimeRequiredError, match="not installed"):
        helper.collect_unified_runtime()


@pytest.mark.parametrize(
    "spec_name", ["vibe.spec", "vibe-acp.spec", "vibe-app-server.spec"]
)
def test_specs_collect_the_runtime_unconditionally(spec_name: str) -> None:
    content = (TESTS_ROOT.parent / spec_name).read_text()
    assert "collect_unified_runtime()" in content
    # The Runtime must never be optional again: a find_spec() guard would
    # reintroduce the dynamically discovered, possibly-absent Harness import.
    assert "find_spec" not in content
