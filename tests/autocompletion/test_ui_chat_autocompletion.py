from __future__ import annotations

from pathlib import Path

import pytest
from textual.content import Content
from textual.style import Style
from textual.widgets import Markdown

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer


@pytest.mark.asyncio
async def test_popup_appears_with_matching_suggestions(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"/com")

        popup_content = str(popup.render())
        assert popup.styles.display == "block"
        assert "/compact" in popup_content
        assert "Compact conversation history by summarizing" in popup_content
        assert chat_input.value == "/com"


@pytest.mark.asyncio
async def test_popup_hides_when_input_cleared(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"/c")
        await pilot.press("backspace", "backspace")

        assert popup.styles.display == "none"


@pytest.mark.asyncio
async def test_pressing_tab_writes_selected_command_and_keeps_popup_visible(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"/co")
        await pilot.press("tab")

        assert chat_input.value == "/compact"
        assert popup.styles.display == "block"


def ensure_selected_command(popup: CompletionPopup, expected_alias: str) -> None:
    renderable = popup.render()
    assert isinstance(renderable, Content)
    content = str(renderable)

    selected_aliases: list[str] = []
    for span in renderable.spans:
        style = span.style
        if isinstance(style, Style) and style.reverse:
            alias_text = content[span.start : span.end].strip()
            alias = alias_text.split()[0] if alias_text else ""
            selected_aliases.append(alias)

    assert len(selected_aliases) == 1
    assert selected_aliases[0] == expected_alias


@pytest.mark.asyncio
async def test_arrow_navigation_updates_selected_suggestion(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"/c")

        ensure_selected_command(popup, "/clear")
        await pilot.press("down")
        ensure_selected_command(popup, "/compact")
        await pilot.press("up")
        ensure_selected_command(popup, "/clear")


@pytest.mark.asyncio
async def test_arrow_navigation_cycles_through_suggestions(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"/co")

        ensure_selected_command(popup, "/compact")
        await pilot.press("down")
        ensure_selected_command(popup, "/config")
        await pilot.press("up")
        ensure_selected_command(popup, "/compact")


@pytest.mark.asyncio
async def test_pressing_enter_submits_selected_command_and_hides_popup(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"/hel")  # typos:disable-line
        await pilot.press("enter")

        assert chat_input.value == ""
        assert popup.styles.display == "none"
        message = vibe_app.query_one(".user-command-message")
        message_content = message.query_one(Markdown)
        assert "Show help message" in message_content.source


@pytest.fixture()
def file_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "src" / "utils" / "config.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "utils" / "database.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "utils" / "error_handling.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "utils" / "logger.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "utils" / "sanitize.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "utils" / "validate.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "vibe" / "acp").mkdir(parents=True)
    (tmp_path / "vibe" / "acp" / "entrypoint.py").write_text("", encoding="utf-8")
    (tmp_path / "vibe" / "acp" / "agent.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_path_completion_popup_lists_files_and_directories(
    vibe_app: VibeApp, file_tree: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"@s")

        popup_content = str(popup.render())
        assert "@src/" in popup_content
        assert popup.styles.display == "block"


@pytest.mark.asyncio
async def test_path_completion_popup_shows_up_to_ten_results(
    vibe_app: VibeApp, file_tree: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        (file_tree / "src" / "core" / "extra").mkdir(parents=True)
        [
            (file_tree / "src" / "core" / "extra" / f"extra_file_{i}.py").write_text(
                "", encoding="utf-8"
            )
            for i in range(1, 13)
        ]
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"@src/core/extra/")

        popup_content = str(popup.render())
        assert "@src/core/extra/extra_file_1.py" in popup_content
        assert "@src/core/extra/extra_file_10.py" in popup_content
        assert "@src/core/extra/extra_file_11.py" in popup_content
        assert "@src/core/extra/extra_file_12.py" in popup_content
        assert "@src/core/extra/extra_file_2.py" in popup_content
        assert "@src/core/extra/extra_file_3.py" in popup_content
        assert "@src/core/extra/extra_file_4.py" in popup_content
        assert "@src/core/extra/extra_file_5.py" in popup_content
        assert "@src/core/extra/extra_file_6.py" in popup_content
        assert "@src/core/extra/extra_file_7.py" in popup_content
        assert popup.styles.display == "block"


@pytest.mark.asyncio
async def test_pressing_tab_writes_selected_path_name_and_hides_popup(
    vibe_app: VibeApp, file_tree: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"Print @REA")
        await pilot.press("tab")

        assert chat_input.value == "Print @README.md "
        assert popup.styles.display == "none"


@pytest.mark.asyncio
async def test_pressing_enter_writes_selected_path_name_and_hides_popup(
    vibe_app: VibeApp, file_tree: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"Print @src/m")
        await pilot.press("enter")

        assert chat_input.value == "Print @src/main.py "
        assert popup.styles.display == "none"


@pytest.mark.asyncio
async def test_fuzzy_matches_subsequence_characters(
    file_tree: Path, vibe_app: VibeApp
) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"@src/utils/handling")

        popup_content = str(popup.render())
        assert "@src/utils/error_handling.py" in popup_content
        assert popup.styles.display == "block"


@pytest.mark.asyncio
async def test_fuzzy_matches_word_boundaries(
    file_tree: Path, vibe_app: VibeApp
) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"@src/utils/eh")

        popup_content = str(popup.render())
        assert "@src/utils/error_handling.py" in popup_content
        assert popup.styles.display == "block"


@pytest.mark.asyncio
async def test_finds_files_recursively_by_filename(
    file_tree: Path, vibe_app: VibeApp
) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"@entryp")

        popup_content = str(popup.render())
        assert "@vibe/acp/entrypoint.py" in popup_content
        assert popup.styles.display == "block"


@pytest.mark.asyncio
async def test_finds_files_recursively_with_partial_path(
    file_tree: Path, vibe_app: VibeApp
) -> None:
    async with vibe_app.run_test() as pilot:
        popup = vibe_app.query_one(CompletionPopup)

        await pilot.press(*"@acp/entry")

        popup_content = str(popup.render())
        assert "@vibe/acp/entrypoint.py" in popup_content
        assert popup.styles.display == "block"


@pytest.mark.asyncio
async def test_does_not_trigger_completion_when_navigating_history(
    file_tree: Path, vibe_app: VibeApp
) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        popup = vibe_app.query_one(CompletionPopup)
        message_with_path = "Check @src/m"
        message_to_fill_history = "Yet another message to fill history"

        await pilot.press(*message_with_path)
        await pilot.press("tab", "enter")
        await pilot.press(*message_to_fill_history)
        await pilot.press("enter")
        await pilot.press("up", "up")
        assert chat_input.value == "Check @src/main.py"
        await pilot.pause(0.2)
        # ensure popup is hidden - user was navigating history: we don't want to interrupt
        assert popup.styles.display == "none"
        await pilot.press("down")
        await pilot.pause(0.1)
        assert popup.styles.display == "none"
        # get back to the message with path completion; ensure again
        await pilot.press("up")
        await pilot.pause(0.1)
        assert chat_input.value == "Check @src/main.py"
        await pilot.pause(0.2)
        assert popup.styles.display == "none"
