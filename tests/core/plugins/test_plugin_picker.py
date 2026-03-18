"""Tests for the PluginPickerApp widget."""

from __future__ import annotations

from vibe.cli.textual_ui.widgets.plugin_picker import (
    PluginPickerApp,
    _build_option_text,
)
from vibe.core.plugins.models import PluginEntry, PluginScope, PluginSource


class TestBuildOptionText:
    def test_enabled_plugin_shows_checkmark(self) -> None:
        entry = PluginEntry(
            name="test-plugin", version="1.0.0", source=PluginSource.GIT, enabled=True
        )
        text = _build_option_text("test-plugin", entry, PluginScope.USER)
        plain = text.plain
        assert "✓" in plain
        assert "test-plugin" in plain
        assert "1.0.0" in plain
        assert "USR" in plain

    def test_disabled_plugin_shows_cross(self) -> None:
        entry = PluginEntry(
            name="test-plugin", version="1.0.0", source=PluginSource.GIT, enabled=False
        )
        text = _build_option_text("test-plugin", entry, PluginScope.USER)
        plain = text.plain
        assert "✗" in plain

    def test_scope_labels(self) -> None:
        entry = PluginEntry(
            name="test-plugin", version="1.0.0", source=PluginSource.LOCAL, enabled=True
        )
        for scope, label in [
            (PluginScope.USER, "USR"),
            (PluginScope.PROJECT, "PRJ"),
            (PluginScope.LOCAL, "LCL"),
        ]:
            text = _build_option_text("test-plugin", entry, scope)
            assert label in text.plain

    def test_source_shown(self) -> None:
        entry = PluginEntry(
            name="test-plugin",
            version="1.0.0",
            source=PluginSource.MARKETPLACE,
            enabled=True,
        )
        text = _build_option_text("test-plugin", entry, PluginScope.USER)
        assert "marketplace" in text.plain


class TestPluginPickerAppMessages:
    def test_plugin_toggled_message(self) -> None:
        msg = PluginPickerApp.PluginToggled("test-plugin", enabled=True)
        assert msg.plugin_name == "test-plugin"
        assert msg.enabled is True

    def test_cancelled_message(self) -> None:
        msg = PluginPickerApp.Cancelled()
        assert isinstance(msg, PluginPickerApp.Cancelled)

    def test_constructor_stores_plugins(self) -> None:
        plugins = {
            "test": (
                PluginScope.USER,
                PluginEntry(
                    name="test", version="1.0.0", source=PluginSource.GIT, enabled=True
                ),
            )
        }
        app = PluginPickerApp(plugins=plugins)
        assert app._plugins == plugins
