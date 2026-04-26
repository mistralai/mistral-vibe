from __future__ import annotations

import sys

import pytest

from vibe.cli.entrypoint import _find_plugin_subcommand_idx


class TestFindPluginSubcommandIdx:
    def test_finds_plugin_after_global_flag_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["vibe", "--workdir", "/tmp/project", "--agent", "default", "plugin"],
        )

        assert _find_plugin_subcommand_idx() == 5

    def test_ignores_plugin_as_prompt_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["vibe", "-p", "plugin"])

        assert _find_plugin_subcommand_idx() is None
