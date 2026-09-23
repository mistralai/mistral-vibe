from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from textual import events
from textual.containers import VerticalScroll
from textual.geometry import Offset

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.word_selection import SelectableScreen
from vibe.setup.trusted_folders.trust_folder_dialog import TrustFolderApp


def _make_app(
    *, files: list[str] | None = None, autocopy_to_clipboard: bool = True
) -> TrustFolderApp:
    return TrustFolderApp(
        cwd=Path("/workspace/project"),
        repo_root=None,
        detected_files=files or ["AGENTS.md"],
        settings_path="/home/user/.vibe/trusted_folders.toml",
        autocopy_to_clipboard=autocopy_to_clipboard,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("shortcut", ["ctrl+y", "ctrl+shift+c"])
async def test_copy_shortcuts_copy_selection(shortcut: str) -> None:
    app = _make_app()

    with patch(
        "vibe.setup.trusted_folders.trust_folder_dialog.copy_selection_to_clipboard"
    ) as copy_selection:
        async with app.run_test() as pilot:
            await pilot.press(shortcut)

    copy_selection.assert_called_once_with(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("autocopy_to_clipboard", "expected_calls"), [(True, 1), (False, 0)]
)
async def test_mouse_up_respects_autocopy_setting(
    autocopy_to_clipboard: bool, expected_calls: int
) -> None:
    app = _make_app(autocopy_to_clipboard=autocopy_to_clipboard)

    with patch(
        "vibe.setup.trusted_folders.trust_folder_dialog.copy_selection_to_clipboard"
    ) as copy_selection:
        async with app.run_test() as pilot:
            await pilot.click(offset=(0, 0))

    assert copy_selection.call_count == expected_calls


@pytest.mark.asyncio
async def test_selection_endpoint_tracks_upward_autoscroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = [f"file{index:02}" for index in range(30)]
    app = _make_app(files=files)
    monkeypatch.setattr(TrustFolderApp, "ENABLE_SELECT_AUTO_SCROLL", False)

    async with app.run_test(size=(80, 40)) as pilot:
        scroll = app.query_one("#trust-dialog-content", VerticalScroll)
        file_list = app.query_one("#trust-dialog-files", NoMarkupStatic)
        screen = cast(SelectableScreen, app.screen)

        scroll.scroll_to(y=16, animate=False)
        await pilot.pause()
        assert scroll.scroll_y == 16

        await pilot.mouse_down(file_list, offset=Offset(33, 19))
        top = scroll.content_region.offset + Offset(30, 0)
        screen._forward_event(
            events.MouseMove(
                widget=file_list,
                x=top.x,
                y=top.y,
                delta_x=0,
                delta_y=-1,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()

        scroll.scroll_to(y=0, animate=False)
        await pilot.pause()
        screen._update_select()

        selection = screen.selections[file_list]
        selected_text, _ = file_list.get_selection(selection) or ("", "")
        footer = app.query_one("#trust-dialog-footer-warning", NoMarkupStatic)

        assert "file00" in selected_text
        assert "file19" in selected_text
        assert footer not in screen.selections
