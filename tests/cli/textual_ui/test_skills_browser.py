from __future__ import annotations

from pathlib import Path

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
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
async def test_versions_view_opens_on_v() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Highlight the installed registry skill (reg-one).
        option_list = widget.query_one(OptionList)
        ids = await _option_ids(widget)
        option_list.highlighted = ids.index("installed:1")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        await pilot.pause()
        ver_ids = await _option_ids(widget)
        assert "alias:latest" in ver_ids
        assert "version:1" in ver_ids


@pytest.mark.asyncio
async def test_p_imports_catalog_skill_into_project_scope() -> None:
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
        await pilot.press("p")
        await pilot.pause()
        await pilot.pause()
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.imports == [("c1", None, None, "project")]


@pytest.mark.asyncio
async def test_p_is_inert_without_a_project() -> None:
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
        await pilot.press("p")
        await pilot.pause()
        actions = widget._actions
        assert isinstance(actions, _FakeActions)
        assert actions.imports == []


@pytest.mark.asyncio
async def test_search_filters_list_and_escape_restores() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Move to the Available tab where the catalog entry "cat-one" lives.
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "cat":
            await pilot.press(ch)
        await pilot.pause()
        ids = await _option_ids(widget)
        assert "catalog:c1" in ids

        # Escape clears the filter and keeps the full Available list.
        await pilot.press("escape")
        await pilot.pause()
        ids = await _option_ids(widget)
        assert "catalog:c1" in ids


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
async def test_updates_tab_lists_only_skills_with_updates() -> None:
    # reg-one is a frozen registry pin (v2); advertise a newer v3.
    widget = _browser(updates={"reg-one": 3})

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        # Installed -> Available -> Updates.
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        ids = await _option_ids(widget)
        assert ids.count("installed:1") == 1
        assert not any(i.startswith("catalog:") for i in ids)


@pytest.mark.asyncio
async def test_updates_tab_empty_when_no_updates() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        assert not any(i for i in await _option_ids(widget))


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
async def test_escape_clears_filter_when_search_focused_by_click() -> None:
    widget = _browser()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        widget.query_one("#skillsbrowser-search", Input).focus()
        await pilot.pause()
        for ch in "cat":
            await pilot.press(ch)
        await pilot.pause()
        assert not any(i.startswith("installed:") for i in await _option_ids(widget))

        await pilot.press("escape")
        await pilot.pause()
        assert any(i.startswith("installed:") for i in await _option_ids(widget))


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
