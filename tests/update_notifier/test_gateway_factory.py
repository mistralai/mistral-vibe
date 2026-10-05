from __future__ import annotations

from pathlib import Path
import sys

import pytest

from vibe.cli.update_notifier.adapters.fallback_update_gateway import (
    FallbackUpdateGateway,
)
from vibe.cli.update_notifier.adapters.pypi_update_gateway import PyPIUpdateGateway
from vibe.cli.update_notifier.gateway_factory import create_update_gateway


@pytest.fixture
def uv_tool_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    return tmp_path


def test_uses_uv_with_pypi_fallback_when_running_from_a_uv_tool_install(
    uv_tool_prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}")

    assert isinstance(create_update_gateway("mistral-vibe"), FallbackUpdateGateway)


def test_falls_back_to_pypi_when_uv_is_not_on_path(
    uv_tool_prefix: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    assert isinstance(create_update_gateway("mistral-vibe"), PyPIUpdateGateway)


def test_uses_pypi_when_not_installed_as_a_uv_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: f"/bin/{name}")

    assert isinstance(create_update_gateway("mistral-vibe"), PyPIUpdateGateway)
