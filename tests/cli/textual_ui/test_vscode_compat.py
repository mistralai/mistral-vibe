"""Tests for VS Code compatibility patches."""

from __future__ import annotations

import os
from unittest.mock import patch

from textual import events

from vibe.cli.textual_ui.widgets.vscode_compat import (
    _is_cursor,
    _is_vscode_insiders,
    _is_vscode_terminal,
    patch_vscode_key,
    patch_vscode_space,
)


class TestPatchVscodeKey:
    """Tests for the patch_vscode_key function."""

    def test_patches_space_when_character_is_none(self):
        """Space key with None character should be patched to ' '."""
        key_event = events.Key(key="space", character=None)

        patch_vscode_key(key_event)

        assert key_event.character == " "

    def test_does_not_patch_space_when_character_exists(self):
        """Space key with existing character should not be modified."""
        key_event = events.Key(key="space", character=" ")

        patch_vscode_key(key_event)

        assert key_event.character == " "

    def test_does_not_patch_other_keys(self):
        """Non-space keys should not be patched."""
        key_event = events.Key(key="enter", character=None)

        patch_vscode_key(key_event)

        assert key_event.character is None


class TestPatchVscodeSpace:
    """Tests for the patch_vscode_space function (backwards compatibility)."""

    def test_delegates_to_patch_vscode_key(self):
        """patch_vscode_space should delegate to patch_vscode_key."""
        key_event = events.Key(key="space", character=None)

        patch_vscode_space(key_event)

        assert key_event.character == " "


class TestIsVscodeTerminal:
    """Tests for VS Code terminal detection."""

    def test_detects_vscode_from_term_program(self):
        """Should detect VS Code when TERM_PROGRAM=vscode."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "vscode"}):
            assert _is_vscode_terminal() is True

    def test_detects_vscode_insiders(self):
        """Should detect VS Code Insiders."""
        with patch.dict(
            os.environ,
            {"TERM_PROGRAM": "vscode", "TERM_PROGRAM_VERSION": "1.111.0-insider"},
        ):
            assert _is_vscode_terminal() is True
            assert _is_vscode_insiders() is True

    def test_detects_cursor(self):
        """Should detect Cursor IDE."""
        with patch.dict(
            os.environ,
            {
                "TERM_PROGRAM": "vscode",
                "VSCODE_GIT_ASKPASS_NODE": "/Applications/Cursor.app/",
            },
        ):
            assert _is_vscode_terminal() is True
            assert _is_cursor() is True

    def test_returns_false_for_other_terminals(self):
        """Should return False for non-VS Code terminals."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=True):
            assert _is_vscode_terminal() is False
