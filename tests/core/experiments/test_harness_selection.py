from __future__ import annotations

from types import SimpleNamespace

import pytest

from vibe._experimental_harness import (
    ExperimentalHarnessUnavailableError,
    create_experimental_harness_host,
    require_experimental_harness,
)


class TestRequireExperimentalHarness:
    def test_passes_when_the_runtime_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vibe._experimental_harness as mod

        monkeypatch.setattr(mod, "experimental_harness_available", lambda: True)
        monkeypatch.setattr(
            mod,
            "create_experimental_harness_host",
            lambda: SimpleNamespace(configure_hook_handlers=lambda _handlers: None),
        )
        require_experimental_harness()

    def test_missing_runtime_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vibe._experimental_harness as mod

        monkeypatch.setattr(mod, "experimental_harness_available", lambda: False)
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            require_experimental_harness()
        message = str(excinfo.value)
        assert "not installed" in message
        assert "--legacy-harness" not in message
        assert "uv tool upgrade mistral-vibe" in message

    def test_incompatible_runtime_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pinned harness whose Host lacks the builtin-hook registry must
        abort the pre-serving probe: ACP cannot surface a mid-session
        failure as a process abort, so the probe covers this case.
        """
        import vibe._experimental_harness as mod

        monkeypatch.setattr(mod, "experimental_harness_available", lambda: True)
        monkeypatch.setattr(
            mod, "create_experimental_harness_host", lambda: SimpleNamespace()
        )
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            require_experimental_harness()
        message = str(excinfo.value)
        assert "configure_hook_handlers" in message
        assert "--legacy-harness" not in message


class TestCreateExperimentalHarnessHost:
    def test_import_failure_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _missing_module(name: str) -> object:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

        monkeypatch.setattr("vibe._experimental_harness.import_module", _missing_module)
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            create_experimental_harness_host()
        assert "could not be loaded" in str(excinfo.value)
        assert "--legacy-harness" not in str(excinfo.value)

    def test_incompatible_runtime_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types

        def _old_runtime(name: str) -> object:
            # A harness version without the create_harness_host factory.
            return types.SimpleNamespace()

        monkeypatch.setattr("vibe._experimental_harness.import_module", _old_runtime)
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            create_experimental_harness_host()
        assert "AttributeError" in str(excinfo.value)

    def test_initialization_failure_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types

        def _failing_runtime(name: str) -> object:
            module = types.SimpleNamespace(
                create_harness_host=lambda: (_ for _ in ()).throw(
                    RuntimeError("native extension incompatible")
                )
            )
            return module

        monkeypatch.setattr(
            "vibe._experimental_harness.import_module", _failing_runtime
        )
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            create_experimental_harness_host()
        message = str(excinfo.value)
        assert "initialization failed" in message
        assert "native extension incompatible" in message
        assert "--legacy-harness" not in message
