from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from vibe.core.config import DEFAULT_THEME
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.setup.update_prompt import load_update_prompt_theme


def _write_config(path: Path, **data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def test_env_theme_overrides_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    _write_config(config_file, theme="textual-light")

    theme = load_update_prompt_theme(
        environ={"VIBE_THEME": "dracula"}, config_file=config_file
    )

    assert theme == "dracula"


def test_config_theme_is_used_when_env_theme_is_missing(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    _write_config(config_file, theme="textual-light")

    theme = load_update_prompt_theme(environ={}, config_file=config_file)

    assert theme == "textual-light"


def test_invalid_theme_falls_back_to_default(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    _write_config(config_file, theme="unknown-theme")

    theme = load_update_prompt_theme(environ={}, config_file=config_file)

    assert theme == DEFAULT_THEME


def test_invalid_env_theme_does_not_fall_through_to_config(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    _write_config(config_file, theme="dracula")

    theme = load_update_prompt_theme(
        environ={"VIBE_THEME": "unknown-theme"}, config_file=config_file
    )

    assert theme == DEFAULT_THEME


def test_trust_aware_config_source_ignores_untrusted_project_theme(
    config_dir: Path, tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_HOME", raising=False)
    _write_config(config_dir / "config.toml", theme="textual-light")
    _write_config(tmp_working_directory / ".vibe" / "config.toml", theme="dracula")

    theme = load_update_prompt_theme(environ={})

    assert theme == "textual-light"


def test_trust_aware_config_source_can_use_trusted_project_theme(
    tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIBE_HOME", raising=False)
    _write_config(tmp_working_directory / ".vibe" / "config.toml", theme="dracula")
    trusted_folders_manager.add_trusted(tmp_working_directory)

    theme = load_update_prompt_theme(environ={})

    assert theme == "dracula"
