"""Tests for the external editor module."""

from __future__ import annotations

from unittest.mock import patch

from vibe.cli.textual_ui.external_editor import ExternalEditor


class TestGetEditor:
    def test_returns_visual_first(self) -> None:
        with patch.dict("os.environ", {"VISUAL": "vim", "EDITOR": "nvim"}, clear=True):
            assert ExternalEditor.get_editor() == "vim"

    def test_falls_back_to_editor(self) -> None:
        with patch.dict("os.environ", {"EDITOR": "nvim"}, clear=True):
            assert ExternalEditor.get_editor() == "nvim"

    def test_falls_back_when_no_editor(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert ExternalEditor.get_editor() == "nano"


class TestEdit:
    def test_returns_modified_content(self) -> None:
        with patch.dict("os.environ", {"VISUAL": "vim"}, clear=True):
            with patch("subprocess.run") as mock_run:
                with patch("pathlib.Path.read_text", return_value="modified"):
                    with patch("pathlib.Path.unlink"):
                        editor = ExternalEditor()
                        result = editor.edit("original")
                        assert result == "modified"
                        mock_run.assert_called_once()

    def test_returns_none_when_content_unchanged(self) -> None:
        with patch.dict("os.environ", {"VISUAL": "vim"}, clear=True):
            with patch("subprocess.run"):
                with patch("pathlib.Path.read_text", return_value="same"):
                    with patch("pathlib.Path.unlink"):
                        editor = ExternalEditor()
                        result = editor.edit("same")
                        assert result is None

    def test_strips_trailing_whitespace(self) -> None:
        with patch.dict("os.environ", {"VISUAL": "vim"}, clear=True):
            with patch("subprocess.run"):
                with patch("pathlib.Path.read_text", return_value="content\n\n"):
                    with patch("pathlib.Path.unlink"):
                        editor = ExternalEditor()
                        result = editor.edit("original")
                        assert result == "content"

    def test_handles_editor_with_args(self) -> None:
        with patch.dict("os.environ", {"VISUAL": "code --wait"}, clear=True):
            with patch("subprocess.run") as mock_run:
                with patch("pathlib.Path.read_text", return_value="edited"):
                    with patch("pathlib.Path.unlink"):
                        editor = ExternalEditor()
                        editor.edit("original")
                        call_args = mock_run.call_args[0][0]
                        assert call_args[0] == "code"
                        assert call_args[1] == "--wait"

    def test_returns_none_on_subprocess_error(self) -> None:
        import subprocess as sp

        with patch.dict("os.environ", {"VISUAL": "vim"}, clear=True):
            with patch("subprocess.run", side_effect=sp.CalledProcessError(1, "vim")):
                with patch("pathlib.Path.unlink"):
                    editor = ExternalEditor()
                    result = editor.edit("test")
                    assert result is None
