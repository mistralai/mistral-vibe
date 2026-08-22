"""Activation: which checkout the bridge attaches to, and how it finds the bundle."""

from __future__ import annotations

from pathlib import Path

import pytest

from accordion_vibe._config import (
    resolve_accordion_app,
    resolve_accordion_repo,
    sidecar_command,
    sidecar_log_path,
)
from tests.conftest import build_test_vibe_config
from vibe.core.config import VibeConfigSchema
from vibe.core.config.models import AccordionConfig


def _config_with_repo(repo: str) -> VibeConfigSchema:
    return build_test_vibe_config(accordion={"repo": repo})


# -- resolve_accordion_repo -------------------------------------------------


def test_no_env_and_no_config_is_inert():
    assert resolve_accordion_repo() is None
    assert resolve_accordion_repo(None) is None


def test_empty_config_repo_is_inert():
    assert resolve_accordion_repo(_config_with_repo("")) is None


def test_env_pointing_at_a_real_directory_wins_over_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from_env = tmp_path / "from-env"
    from_config = tmp_path / "from-config"
    from_env.mkdir()
    from_config.mkdir()
    monkeypatch.setenv("ACCORDION_REPO", str(from_env))

    assert resolve_accordion_repo(_config_with_repo(str(from_config))) == from_env


def test_env_pointing_at_a_missing_directory_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("ACCORDION_REPO", str(tmp_path / "nope"))

    assert resolve_accordion_repo() is None


def test_env_pointing_at_a_file_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    target = tmp_path / "a-file"
    target.write_text("", encoding="utf-8")
    monkeypatch.setenv("ACCORDION_REPO", str(target))

    assert resolve_accordion_repo() is None


def test_config_repo_is_used_when_the_env_is_unset(tmp_path: Path):
    repo = tmp_path / "checkout"
    repo.mkdir()

    assert resolve_accordion_repo(_config_with_repo(str(repo))) == repo


def test_config_repo_pointing_at_a_missing_directory_is_none(tmp_path: Path):
    assert resolve_accordion_repo(_config_with_repo(str(tmp_path / "gone"))) is None


# -- resolve_accordion_app --------------------------------------------------


def test_accordion_app_prefers_the_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACCORDION_APP", "  /bin/from-env  ")
    config = build_test_vibe_config(accordion={"app": "/bin/from-config"})

    assert resolve_accordion_app(config) == "/bin/from-env"


def test_accordion_app_is_none_when_unset():
    assert resolve_accordion_app() is None
    assert resolve_accordion_app(build_test_vibe_config(accordion={"app": ""})) is None


# -- sidecar_command --------------------------------------------------------


def _bundle(repo: Path, relative: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// bundle", encoding="utf-8")
    return path


def test_sidecar_command_prefers_the_top_level_bundle(tmp_path: Path):
    preferred = _bundle(tmp_path, "extension/sidecar.mjs")
    _bundle(tmp_path, "extension/dist/sidecar.mjs")

    assert sidecar_command(tmp_path) == ["node", str(preferred)]


def test_sidecar_command_falls_back_to_dist(tmp_path: Path):
    fallback = _bundle(tmp_path, "extension/dist/sidecar.mjs")

    assert sidecar_command(tmp_path) == ["node", str(fallback)]


def test_sidecar_command_is_none_when_no_bundle_exists(tmp_path: Path):
    assert sidecar_command(tmp_path) is None


def test_sidecar_command_ignores_a_directory_named_like_the_bundle(tmp_path: Path):
    (tmp_path / "extension" / "sidecar.mjs").mkdir(parents=True)

    assert sidecar_command(tmp_path) is None


# -- sidecar_log_path -------------------------------------------------------


def test_sidecar_log_path_sanitises_the_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # The helper mkdir -p's under Path.home(); keep that off the real home.
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    path = sidecar_log_path("a/b:c d")

    assert path.name == "vibe-sidecar-a_b_c_d.log"
    assert path.parent == tmp_path / ".accordion" / "logs"
    assert path.parent.is_dir()


# -- regression: BaseSettings env sourcing ----------------------------------


def test_a_bare_home_env_var_does_not_configure_accordion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``AccordionConfig`` must not read the ubiquitous ``HOME``/``APP`` vars.

    It is a ``BaseSettings``, so without ``env_prefix="ACCORDION_"`` the field
    named ``home`` would be populated from bare ``HOME``, and the bridge
    itself on for every POSIX user.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APP", "somewhere")

    assert AccordionConfig().repo == ""
    assert AccordionConfig().app == ""


def test_the_prefixed_env_vars_do_configure_accordion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("ACCORDION_REPO", str(tmp_path))
    monkeypatch.setenv("ACCORDION_APP", "app.exe")

    assert AccordionConfig().repo == str(tmp_path)
    assert AccordionConfig().app == "app.exe"
