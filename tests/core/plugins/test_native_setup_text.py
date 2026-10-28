"""The ``ai.mistral.vibe`` extension's ``setupText``: manifest validation,
scope precedence, and the per-locale welcome text the onboarding wizard
shows in place of its default.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError
import pytest

from vibe.core.plugins import PluginResolver
from vibe.core.plugins._compatibility import DetectedPluginFormat
from vibe.core.plugins._native import (
    PluginDescriptor,
    _VibePluginExtension,
    resolve_setup_text as resolve_setup_text_precedence,
)
from vibe.core.skills.models import SkillScope
from vibe.setup.onboarding.context import (
    SETUP_LOCALE_ENV_VAR,
    resolve_setup_locale,
    resolve_setup_text,
)

_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_LOCALE_ENV_VARS = (SETUP_LOCALE_ENV_VAR, "LC_ALL", "LC_MESSAGES", "LANG")


def _extension_payload(locales: dict[str, str]) -> dict[str, Any]:
    return {"schemaVersion": 1, "setupText": {"locales": locales}}


def _write_plugin(root: Path, name: str, extension: dict[str, Any] | None) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    manifest: dict[str, Any] = {"$schema": _SCHEMA, "name": name}
    if extension is not None:
        manifest["extensions"] = {"ai.mistral.vibe": extension}
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return plugin_dir


@pytest.fixture(autouse=True)
def _clear_locale_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LOCALE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestSetupTextValidation:
    def test_a_valid_locales_map_parses(self) -> None:
        extension = _VibePluginExtension.model_validate(
            _extension_payload({"en": "Welcome to Acme!", "fr": "Bienvenue !"})
        )
        assert extension.setup_text is not None
        assert extension.setup_text.locales == {
            "en": "Welcome to Acme!",
            "fr": "Bienvenue !",
        }

    def test_en_is_mandatory(self) -> None:
        with pytest.raises(ValidationError, match="must include 'en'"):
            _VibePluginExtension.model_validate(_extension_payload({"fr": "Bonjour !"}))

    def test_a_locale_tag_must_be_bcp47_style(self) -> None:
        with pytest.raises(ValidationError, match="invalid setupText locale tag"):
            _VibePluginExtension.model_validate(
                _extension_payload({"en": "Hi", "FR!!": "Salut"})
            )

    def test_a_locale_text_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError, match="1-300 characters"):
            _VibePluginExtension.model_validate(_extension_payload({"en": ""}))

    def test_a_locale_text_is_capped_at_300_characters(self) -> None:
        with pytest.raises(ValidationError, match="1-300 characters"):
            _VibePluginExtension.model_validate(_extension_payload({"en": "x" * 301}))

    def test_at_most_32_locales(self) -> None:
        locales = {"en": "Hi", **{f"x{i:02d}": "y" for i in range(32)}}
        with pytest.raises(ValidationError, match="32-locale limit"):
            _VibePluginExtension.model_validate(_extension_payload(locales))

    def test_unknown_extension_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _VibePluginExtension.model_validate({
                "schemaVersion": 1,
                "setupText": {"locales": {"en": "Hi"}, "bogus": 1},
            })


class TestResolverSetupText:
    def test_a_plugin_without_the_extension_has_no_setup_text(
        self, tmp_path: Path
    ) -> None:
        plugins_root = tmp_path / "plugins"
        _write_plugin(plugins_root, "plain", None)
        resolution = PluginResolver(project_roots=[plugins_root]).resolve()
        assert resolution.setup_text is None
        assert resolution.plugins[0].setup_text is None

    def test_a_valid_extension_lands_on_the_descriptor_and_the_set(
        self, tmp_path: Path
    ) -> None:
        plugins_root = tmp_path / "plugins"
        _write_plugin(
            plugins_root,
            "acme",
            _extension_payload({"en": "Welcome to Acme!", "fr": "Bienvenue !"}),
        )
        resolution = PluginResolver(project_roots=[plugins_root]).resolve()
        assert not resolution.issues
        assert resolution.setup_text == {"en": "Welcome to Acme!", "fr": "Bienvenue !"}
        assert resolution.plugins[0].setup_text == resolution.setup_text

    def test_an_invalid_extension_is_an_issue_and_the_plugin_still_loads(
        self, tmp_path: Path
    ) -> None:
        plugins_root = tmp_path / "plugins"
        _write_plugin(plugins_root, "bad", _extension_payload({"fr": "no en"}))
        resolution = PluginResolver(project_roots=[plugins_root]).resolve()
        assert resolution.setup_text is None
        assert [plugin.name for plugin in resolution.plugins] == ["bad"]
        assert resolution.plugins[0].setup_text is None
        assert any(
            "Failed to load ai.mistral.vibe extension" in issue.message
            for issue in resolution.issues
        )

    def test_project_beats_user_beats_builtin(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project" / "plugins"
        user_root = tmp_path / "user" / "plugins"
        builtin_root = tmp_path / "builtin" / "plugins"
        _write_plugin(project_root, "acme", _extension_payload({"en": "project text"}))
        _write_plugin(user_root, "corp", _extension_payload({"en": "user text"}))
        _write_plugin(builtin_root, "stock", _extension_payload({"en": "builtin text"}))
        resolution = PluginResolver(
            project_roots=[project_root],
            user_roots=[user_root],
            builtin_roots=[builtin_root],
        ).resolve()
        assert resolution.setup_text == {"en": "project text"}

    def test_user_beats_builtin_when_the_project_has_none(self, tmp_path: Path) -> None:
        user_root = tmp_path / "user" / "plugins"
        builtin_root = tmp_path / "builtin" / "plugins"
        _write_plugin(user_root, "corp", _extension_payload({"en": "user text"}))
        _write_plugin(builtin_root, "stock", _extension_payload({"en": "builtin text"}))
        resolution = PluginResolver(
            project_roots=[], user_roots=[user_root], builtin_roots=[builtin_root]
        ).resolve()
        assert resolution.setup_text == {"en": "user text"}


def _descriptor(name: str, scope: SkillScope, setup_text: dict[str, str] | None):
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        description=name,
        root=Path("/nonexistent") / name,
        manifest_path=Path("/nonexistent") / name / "plugin.json",
        data_root=Path("/nonexistent") / name,
        namespace=name,
        scope=scope,
        source_format=DetectedPluginFormat.AGENT_PLUGINS_1_0,
        tool_overrides=MappingProxyType({}),
        private_metadata=MappingProxyType({}),
        manifest_digest="",
        content_digest="",
        setup_text=MappingProxyType(setup_text) if setup_text is not None else None,
    )


class TestResolveSetupTextPrecedence:
    def test_the_first_scope_with_a_setup_text_wins(self) -> None:
        plugins = [
            _descriptor("aaa", SkillScope.GLOBAL, {"en": "user"}),
            _descriptor("bbb", SkillScope.PROJECT, {"en": "project"}),
            _descriptor("ccc", SkillScope.BUILTIN, {"en": "builtin"}),
        ]
        # `resolve()` hands the descriptors over sorted by name; precedence is
        # scope-based, so the project plugin wins regardless of name order.
        assert resolve_setup_text_precedence(plugins) == {"en": "project"}

    def test_no_setup_text_anywhere_resolves_none(self) -> None:
        plugins = [
            _descriptor("aaa", SkillScope.PROJECT, None),
            _descriptor("bbb", SkillScope.GLOBAL, None),
        ]
        assert resolve_setup_text_precedence(plugins) is None


class TestLocaleResolution:
    def test_vibe_locale_wins_over_the_lc_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        monkeypatch.setenv(SETUP_LOCALE_ENV_VAR, "de")
        assert resolve_setup_locale() == "de"

    def test_tags_normalize_to_bcp47_style(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        assert resolve_setup_locale() == "fr-fr"

    def test_c_and_posix_are_not_locales(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LC_ALL", "C")
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        assert resolve_setup_locale() == "fr-fr"

    def test_no_locale_env_falls_back_to_en(self) -> None:
        assert resolve_setup_locale() == "en"

    def test_exact_tag_beats_language_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(SETUP_LOCALE_ENV_VAR, "fr-CA")
        locales = {"en": "Hello!", "fr": "Bonjour !", "fr-ca": "Salut le Canada !"}
        assert resolve_setup_text(locales) == "Salut le Canada !"

    def test_language_only_matches_a_regional_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(SETUP_LOCALE_ENV_VAR, "fr-CA")
        assert resolve_setup_text({"en": "Hello!", "fr": "Bonjour !"}) == "Bonjour !"

    def test_an_unmatched_locale_uses_en(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SETUP_LOCALE_ENV_VAR, "de")
        assert resolve_setup_text({"en": "Hello!", "fr": "Bonjour !"}) == "Hello!"

    def test_the_lc_chain_normalizes_underscores_and_encodings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        assert resolve_setup_text({"en": "Hello!", "fr": "Bonjour !"}) == "Bonjour !"
