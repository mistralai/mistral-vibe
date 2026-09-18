from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from vibe.app_server.models import (
    RegistryRefView,
    SkillCatalogEntry,
    SkillSummary,
    SkillVersionView,
)
from vibe.app_server.protocol import SkillsDetailResponse
import vibe.cli.textual_ui.app as app_module
from vibe.cli.textual_ui.widgets.skills_browser import SkillsBrowserApp


class _FakeActions:
    def __init__(self) -> None:
        self.imports: list[tuple[str, int | None, str | None, str]] = []
        self.enabled_calls: list[tuple[str, bool]] = []

    async def detail(
        self, skill_id: str, *, version: int | None = None
    ) -> SkillsDetailResponse:
        return SkillsDetailResponse(detail=None, body="# body")

    async def versions(self, skill_id: str) -> list[SkillVersionView]:
        return [SkillVersionView(version=1)]

    async def import_skill(
        self,
        skill_id: str,
        *,
        version: int | None = None,
        alias: str | None = None,
        scope: str = "global",
    ) -> list[SkillSummary]:
        self.imports.append((skill_id, version, alias, scope))
        return []

    async def set_version(
        self, name: str, version: int, scope: str
    ) -> list[SkillSummary]:
        return []

    async def set_latest(self, name: str, scope: str) -> list[SkillSummary]:
        return []

    async def set_alias(self, name: str, alias: str, scope: str) -> list[SkillSummary]:
        return []

    async def remove(self, name: str, scope: str) -> list[SkillSummary]:
        return []

    async def set_enabled(self, name: str, enabled: bool) -> list[SkillSummary]:
        self.enabled_calls.append((name, enabled))
        return []

    async def read_installed(self) -> list[SkillSummary]:
        return []


def _installed() -> list[SkillSummary]:
    return [
        SkillSummary(
            name="local-one", description="a local skill", prompt="body", source="local"
        ),
        SkillSummary(
            name="reg-one",
            description="a registry skill",
            prompt="body",
            source="registry",
            registry=RegistryRefView(skill_id="cid", version=2, alias=None),
        ),
    ]


def _catalog() -> list[SkillCatalogEntry]:
    return [
        SkillCatalogEntry(
            name="cat-one", skill_id="c1", description="importable", latest_version=1
        )
    ]


def _browser(**kwargs: object) -> SkillsBrowserApp:
    actions = _FakeActions()
    return SkillsBrowserApp(
        actions=actions,
        installed=kwargs.get("installed", _installed()),  # type: ignore[arg-type]
        catalog=kwargs.get("catalog", _catalog()),  # type: ignore[arg-type]
        updates=kwargs.get("updates", {}),  # type: ignore[arg-type]
        on_changed=actions.read_installed,
        project_available=bool(kwargs.get("project_available", False)),
        catalog_loaded=True,
    )


async def _option_ids(widget: SkillsBrowserApp) -> list[str]:
    option_list = widget.query_one(OptionList)
    return [
        option_list.get_option_at_index(i).id or ""
        for i in range(option_list.option_count)
    ]


async def _installed_index(widget: SkillsBrowserApp, name: str) -> int:
    """Row position of the installed skill called *name*.

    Rows are identified by name, scope and source rather than position, so a
    test says which skill it means instead of where it happens to sit.
    """
    ids = await _option_ids(widget)
    return next(
        i
        for i, option_id in enumerate(ids)
        if option_id.startswith(f"installed:{name}|")
    )


async def _installed_id(widget: SkillsBrowserApp, name: str) -> str:
    ids = await _option_ids(widget)
    return next(i for i in ids if i.startswith(f"installed:{name}|"))


@pytest.mark.asyncio
async def test_browser_lists_installed_and_catalog() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Default tab is Installed.
        ids = await _option_ids(widget)
        assert any(i.startswith("installed:") for i in ids)
        assert not any(i.startswith("catalog:") for i in ids)
        # Right arrow switches to the Available tab (importable catalog).
        await pilot.press("right")
        await pilot.pause()
        ids = await _option_ids(widget)
        assert any(i.startswith("catalog:") for i in ids)


@pytest.mark.asyncio
async def test_browser_shows_message_when_empty() -> None:
    widget = _browser(installed=[], catalog=[])

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        assert option_list.option_count == 1
        assert not any(i for i in await _option_ids(widget))


@pytest.mark.asyncio
async def test_catalog_load_failure_opens_available_with_error() -> None:
    actions = _FakeActions()
    widget = SkillsBrowserApp(
        actions=actions,
        installed=[],
        catalog=[],
        updates={},
        on_changed=actions.read_installed,
        catalog_loaded=False,  # registry catalog failed to load
    )

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Nothing installed and the catalog failed: surface the error on Available
        # rather than a bare "No skills installed" on the Installed tab.
        assert widget._tab == "tab-available"
        option_list = widget.query_one(OptionList)
        message = option_list.get_option_at_index(0).prompt
        assert "load" in str(message).casefold()


@pytest.mark.asyncio
async def test_versions_view_opens_on_v() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Highlight the installed registry skill (reg-one).
        option_list = widget.query_one(OptionList)
        await _option_ids(widget)
        option_list.highlighted = await _installed_index(widget, "reg-one")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        await pilot.pause()
        ver_ids = await _option_ids(widget)
        assert "alias:latest" in ver_ids
        assert "version:1" in ver_ids


@pytest.mark.asyncio
async def test_import_flow_catalog_then_version_then_project_scope() -> None:
    widget = _browser(installed=[], project_available=True)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        ids = await _option_ids(widget)
        option_list.highlighted = ids.index("catalog:c1")
        await pilot.pause()
        # Step 1: Enter on a catalog row opens the version step.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        version_ids = await _option_ids(widget)
        assert "alias:latest" in version_ids and "version:1" in version_ids
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.imports == []
        # Step 2: choosing a version leads to the Global/Project scope chooser.
        option_list.highlighted = version_ids.index("alias:latest")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        chooser_ids = await _option_ids(widget)
        assert chooser_ids == ["scope:global", "scope:project"]
        assert actions.imports == []
        # Step 3: choose Project and the import commits.
        option_list.highlighted = chooser_ids.index("scope:project")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert actions.imports == [("c1", None, None, "project")]


@pytest.mark.asyncio
async def test_import_flow_without_project_skips_scope_chooser() -> None:
    widget = _browser(installed=[], project_available=False)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        ids = await _option_ids(widget)
        option_list.highlighted = ids.index("catalog:c1")
        await pilot.pause()
        await pilot.press("enter")  # version step
        await pilot.pause()
        await pilot.pause()
        version_ids = await _option_ids(widget)
        option_list.highlighted = version_ids.index("alias:latest")
        await pilot.pause()
        # No project means no scope chooser: choosing a version imports globally.
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.imports == [("c1", None, None, "global")]


@pytest.mark.asyncio
async def test_import_flow_backspace_steps_back_version_then_list() -> None:
    widget = _browser(installed=[], project_available=True)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        ids = await _option_ids(widget)
        option_list.highlighted = ids.index("catalog:c1")
        await pilot.pause()
        await pilot.press("enter")  # version step
        await pilot.pause()
        await pilot.pause()
        option_list.highlighted = (await _option_ids(widget)).index("alias:latest")
        await pilot.pause()
        await pilot.press("enter")  # scope chooser
        await pilot.pause()
        assert await _option_ids(widget) == ["scope:global", "scope:project"]
        # Backspace cancels the scope chooser back to the version step.
        await pilot.press("backspace")
        await pilot.pause()
        assert "alias:latest" in await _option_ids(widget)
        # Backspace again returns to the catalog list.
        await pilot.press("backspace")
        await pilot.pause()
        assert "catalog:c1" in await _option_ids(widget)
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.imports == []


@pytest.mark.asyncio
async def test_search_row_filters_and_down_returns_to_list() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        search = widget.query_one(Input)
        option_list = widget.query_one(OptionList)
        # `/` moves focus onto the search row; typing there filters (reg, not local).
        await pilot.press("slash")
        await pilot.pause()
        assert search.has_focus
        for ch in "reg":
            await pilot.press(ch)
        await pilot.pause()
        ids = await _option_ids(widget)
        assert await _installed_id(widget, "reg-one") in ids
        assert not any(i.startswith("installed:local-one|") for i in ids)

        # Down leaves the search row for the list, landing on the first row.
        await pilot.press("down")
        await pilot.pause()
        assert option_list.has_focus
        assert not search.has_focus
        assert option_list.highlighted == await _installed_index(widget, "reg-one")


@pytest.mark.asyncio
async def test_up_at_top_of_list_focuses_search_row() -> None:
    installed = [
        SkillSummary(name=f"jog-{n}", description="d", prompt="b", source="local")
        for n in range(4)
    ]
    widget = _browser(installed=installed, catalog=[])

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        search = widget.query_one(Input)
        assert option_list.has_focus
        # Move down, then back up to the first row.
        await pilot.press("down")
        await pilot.pause()
        assert option_list.highlighted == 1
        await pilot.press("up")
        await pilot.pause()
        assert option_list.highlighted == 0
        assert option_list.has_focus
        # Up again from the first row jumps onto the search row above.
        await pilot.press("up")
        await pilot.pause()
        assert search.has_focus


@pytest.mark.asyncio
async def test_search_no_match_shows_message() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "zzz":
            await pilot.press(ch)
        await pilot.pause()
        assert not any(i for i in await _option_ids(widget))


@pytest.mark.asyncio
async def test_ctrl_c_clears_filter_and_keeps_search_focus() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        search = widget.query_one(Input)
        await pilot.press("slash")
        await pilot.pause()
        for ch in "reg":
            await pilot.press(ch)
        await pilot.pause()
        assert not any(
            i.startswith("installed:local-one|") for i in await _option_ids(widget)
        )
        # Ctrl+C clears the filter and stays on the search row (does not defocus
        # to the app's interrupt/quit).
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert search.value == ""
        assert widget._query == ""
        assert search.has_focus
        ids = await _option_ids(widget)
        assert any(i.startswith("installed:local-one|") for i in ids)
        assert any(i.startswith("installed:reg-one|") for i in ids)


@pytest.mark.asyncio
async def test_no_results_hides_preview_and_stays_on_search() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        search = widget.query_one(Input)
        option_list = widget.query_one(OptionList)
        preview = widget.query_one("#skillsbrowser-preview", VerticalScroll)
        assert preview.display  # visible with results
        # Filter to nothing.
        await pilot.press("slash")
        await pilot.pause()
        for ch in "zzz":
            await pilot.press(ch)
        await pilot.pause()
        assert not any(i for i in await _option_ids(widget))
        # Preview pane (and its separator) is hidden when there is nothing to show.
        assert not preview.display
        # Down does not drop into the empty list; focus stays on the search row.
        await pilot.press("down")
        await pilot.pause()
        assert search.has_focus
        assert not option_list.has_focus
        # Clearing the filter brings results and the preview back.
        for _ in range(3):
            await pilot.press("backspace")
        await pilot.pause()
        assert preview.display


@pytest.mark.asyncio
async def test_installed_row_marks_available_update() -> None:
    # reg-one is a frozen registry pin (v2); advertise a newer v3. The marker is
    # shown inline on the Installed row (no separate Updates tab).
    widget = _browser(updates={"reg-one": 3})

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        await _option_ids(widget)
        row = option_list.get_option_at_index(
            await _installed_index(widget, "reg-one")
        ).prompt
        assert isinstance(row, Text)
        assert "New v3" in row.plain


@pytest.mark.asyncio
async def test_available_tab_lists_the_whole_catalog_without_a_load_more_row() -> None:
    catalog = [
        SkillCatalogEntry(
            name=f"cat-{n:03d}", skill_id=f"c{n}", description="d", latest_version=1
        )
        for n in range(120)
    ]
    widget = _browser(installed=[], catalog=catalog)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        ids = await _option_ids(widget)
        assert len([i for i in ids if i.startswith("catalog:")]) == 120
        assert not any(i.startswith("more:") for i in ids)


@pytest.mark.asyncio
async def test_browser_layout_fits_with_real_css() -> None:
    # Regression: the tab bar must be a single line and the option list must
    # actually render (a heavyweight tabs widget once filled the whole screen).
    css_path = str(Path(app_module.__file__).parent / "app.tcss")
    widget = _browser()

    class _App(App[None]):
        CSS_PATH = css_path

        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        tabs = widget.query_one("#skillsbrowser-tabs")
        options = widget.query_one("#skillsbrowser-options")
        content = widget.query_one("#skillsbrowser-content")
        assert tabs.size.height <= 2
        assert options.size.height > 0
        assert content.size.height < 40


@pytest.mark.asyncio
async def test_toggle_enabled_calls_set_enabled() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Highlight an installed skill (reg-one, enabled) and turn it off.
        option_list = widget.query_one(OptionList)
        await _option_ids(widget)
        option_list.highlighted = await _installed_index(widget, "reg-one")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.enabled_calls == [("reg-one", False)]


@pytest.mark.asyncio
async def test_disabled_skill_shows_off_and_toggles_on() -> None:
    installed = [
        SkillSummary(
            name="reg-one",
            description="d",
            prompt="body",
            source="registry",
            scope="global",
            registry=RegistryRefView(skill_id="cid", version=2, alias=None),
            enabled=False,
        )
    ]
    widget = _browser(installed=installed, catalog=[])

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        option_list.highlighted = await _installed_index(widget, "reg-one")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.enabled_calls == [("reg-one", True)]


@pytest.mark.asyncio
async def test_search_escape_leaves_search_keeps_filter_without_closing() -> None:
    closed: list[bool] = []
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

        def on_skills_browser_app_closed(self, _m: SkillsBrowserApp.Closed) -> None:
            closed.append(True)

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "reg":
            await pilot.press(ch)
        await pilot.pause()
        assert not any(
            i.startswith("installed:local-one|") for i in await _option_ids(widget)
        )

        # Escape leaves search but keeps the filter (never resets); must NOT close.
        await pilot.press("escape")
        await pilot.pause()
        ids = await _option_ids(widget)
        assert any(i.startswith("installed:reg-one|") for i in ids)
        assert not any(i.startswith("installed:local-one|") for i in ids)
        assert not widget._searching
        assert widget.query_one(OptionList).has_focus
        assert not closed

        # A second escape (no longer searching) closes the browser.
        await pilot.press("escape")
        await pilot.pause()
        assert closed


@pytest.mark.asyncio
async def test_escape_leaves_search_when_focused_by_click() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        search = widget.query_one("#skillsbrowser-search", Input)

        search.focus()
        await pilot.pause()
        assert widget._searching

        assert widget.try_escape_search() is True
        await pilot.pause()
        assert not search.has_focus
        assert not widget._searching


@pytest.mark.asyncio
async def test_update_badge_clears_once_pinned_to_latest() -> None:
    installed = [
        SkillSummary(
            name="reg-one",
            description="d",
            prompt="body",
            source="registry",
            scope="global",
            registry=RegistryRefView(skill_id="cid", version=3, alias=None),
        )
    ]
    widget = _browser(installed=installed, catalog=[], updates={"reg-one": 3})

    assert widget._has_update(installed[0]) is False


@pytest.mark.asyncio
async def test_globally_pinned_skill_stays_importable_for_the_project() -> None:
    catalog = [
        SkillCatalogEntry(
            name="reg-one", skill_id="cid", description="d", latest_version=2
        )
    ]
    installed = [
        SkillSummary(
            name="reg-one",
            description="d",
            prompt="body",
            source="registry",
            scope="global",
            registry=RegistryRefView(skill_id="cid", version=2, alias=None),
        )
    ]
    widget = _browser(installed=installed, catalog=catalog, project_available=True)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert "catalog:cid" in await _option_ids(widget)


@pytest.mark.asyncio
async def test_globally_pinned_skill_is_hidden_without_a_project() -> None:
    catalog = [
        SkillCatalogEntry(
            name="reg-one", skill_id="cid", description="d", latest_version=2
        )
    ]
    installed = [
        SkillSummary(
            name="reg-one",
            description="d",
            prompt="body",
            source="registry",
            scope="global",
            registry=RegistryRefView(skill_id="cid", version=2, alias=None),
        )
    ]
    widget = _browser(installed=installed, catalog=catalog, project_available=False)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert "catalog:cid" not in await _option_ids(widget)


@pytest.mark.asyncio
async def test_search_query_with_markup_is_shown_verbatim() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        widget._query = "[/]"
        message = widget._empty_message()
        assert isinstance(message, Text)
        assert "[/]" in message.plain

        option_list = widget.query_one(OptionList)
        option_list.add_option(Option(message, disabled=True))
        await pilot.pause()


@pytest.mark.asyncio
async def test_list_has_focus_on_open_so_navigation_works_immediately() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        assert widget.query_one(OptionList).has_focus


@pytest.mark.asyncio
async def test_resize_does_not_steal_focus_while_searching() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        search = widget.query_one("#skillsbrowser-search", Input)
        search.focus()
        await pilot.pause()
        assert search.has_focus

        await pilot.resize_terminal(70, 24)
        await pilot.pause()
        assert search.has_focus


def _dual_scope_pins() -> list[SkillSummary]:
    return [
        SkillSummary(
            name="reg-one",
            description="d",
            prompt="body",
            source="registry",
            scope="global",
            registry=RegistryRefView(skill_id="cid", version=2, alias=None),
        ),
        SkillSummary(
            name="reg-one",
            description="d",
            prompt="body",
            source="registry",
            scope="project",
            registry=RegistryRefView(skill_id="cid", version=3, alias=None),
        ),
    ]


@pytest.mark.asyncio
async def test_a_skill_pinned_in_both_scopes_shows_one_row_but_keeps_both_pins() -> (
    None
):
    """Prepare the same skill pinned globally and in the project.

    Do open the browser.

    Assert the list shows a single row, the project pin, and that both pins are
    still reachable. The agent resolves a name to one skill so two rows would
    misrepresent it, but collapsing the underlying pins is what made the global
    one impossible to see or remove.
    """
    widget = _browser(installed=_dual_scope_pins(), catalog=[], project_available=True)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        rows = [i for i in await _option_ids(widget) if i.startswith("installed:")]

        assert rows == ["installed:reg-one|project|registry"]
        assert {i.scope for i in widget._installed if i.name == "reg-one"} == {
            "global",
            "project",
        }


@pytest.mark.asyncio
async def test_the_cursor_follows_the_skill_not_the_row_position() -> None:
    """Prepare a list and highlight the second row.

    Do drop the row above it, as a removal would.

    Assert the cursor is still on the same skill. Restoring by position leaves
    it pointing at whatever moved up, so the next remove takes a skill the user
    never highlighted.
    """
    widget = _browser(catalog=[])

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        option_list.highlighted = await _installed_index(widget, "reg-one")
        await pilot.pause()
        started_on = widget._highlighted_installed()
        assert started_on is not None and started_on.name == "reg-one"

        widget._installed = [i for i in widget._installed if i.name != "local-one"]
        widget._show_list()
        await pilot.pause()

        still = widget._highlighted_installed()
        assert still is not None and still.name == "reg-one"


@pytest.mark.asyncio
async def test_removing_the_highlighted_skill_leaves_the_cursor_on_its_neighbour() -> (
    None
):
    """Prepare a list and highlight the last row.

    Do drop that row, as removing the highlighted skill would.

    Assert the cursor lands on the row that took its place rather than jumping
    back to the top. The skill it pointed at is gone, so position is the next
    best answer, and it is the one that does not move the user's hands.
    """
    widget = _browser(catalog=[])

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        option_list.highlighted = await _installed_index(widget, "reg-one")
        await pilot.pause()
        last = option_list.highlighted

        widget._installed = [i for i in widget._installed if i.name != "reg-one"]
        widget._show_list()
        await pilot.pause()

        assert option_list.highlighted is not None
        assert option_list.highlighted <= last
        remaining = widget._highlighted_installed()
        assert remaining is not None and remaining.name == "local-one"


def _local_file_shadowing_a_project_pin() -> list[SkillSummary]:
    return [
        SkillSummary(
            name="shared",
            description="d",
            prompt="body",
            source="registry",
            scope="project",
            registry=RegistryRefView(skill_id="cid", version=2, alias=None),
        ),
        SkillSummary(
            name="shared",
            description="d",
            prompt="body",
            source="local",
            scope="global",
        ),
    ]


@pytest.mark.asyncio
async def test_a_global_local_file_outranks_a_project_registry_pin() -> None:
    """Prepare one name held by a global local file and a project registry pin.

    Do open the browser.

    Assert the row shown is the local file. Discovery adds local files before it
    considers a registry pin at all, so scope cannot outrank source, and ranking
    scope first advertised a row the agent would never load.
    """
    widget = _browser(
        installed=_local_file_shadowing_a_project_pin(),
        catalog=[],
        project_available=True,
    )

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        rows = [i for i in await _option_ids(widget) if i.startswith("installed:")]

        assert rows == ["installed:shared|global|local"]


def _three_importable() -> list[SkillCatalogEntry]:
    return [
        SkillCatalogEntry(
            name=name, skill_id=skill_id, description="importable", latest_version=1
        )
        for name, skill_id in (("alpha", "c1"), ("bravo", "c2"), ("charlie", "c3"))
    ]


@pytest.mark.asyncio
async def test_switching_tabs_starts_at_the_top_not_the_old_row_index() -> None:
    """Prepare an installed list and highlight a row below the first.

    Do switch to the Available tab.

    Assert the cursor sits on the first entry. The remembered index belongs to
    the list the user left, so carrying it across lands on an unrelated skill
    that happens to share a position.
    """
    widget = _browser(catalog=_three_importable())

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        option_list = widget.query_one(OptionList)
        option_list.highlighted = await _installed_index(widget, "reg-one")
        await pilot.pause()
        assert option_list.highlighted == 1

        widget.action_next_tab()
        await pilot.pause()

        assert widget._tab == "tab-available"
        assert option_list.highlighted == 0
        assert widget._highlighted_id() == "catalog:c1"
