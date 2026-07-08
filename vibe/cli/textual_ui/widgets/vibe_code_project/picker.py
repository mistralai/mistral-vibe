from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput
from vibe.core.vibe_code_project import (
    ProjectMatchKind,
    ProjectPickerContext,
    ProjectPickerCreateItem,
    ProjectPickerItem,
    ProjectPickerLoadMoreItem,
    ProjectPickerProjectItem,
    ProjectPickerUnlinkItem,
    VibeCodeProject,
    build_project_picker_items,
    repo_url_label,
)

_MIN_NAME_COLUMN_WIDTH = 28
_MAX_NAME_COLUMN_WIDTH = 48
_REPO_COUNT_COLUMN_WIDTH = 8
_COLUMN_GAP = 3


def _build_repo_text(context: ProjectPickerContext) -> Text:
    text = Text(no_wrap=True)
    text.append("Repository: ", style="dim")
    text.append(repo_url_label(context.repo_url))
    return text


def _build_scope_text() -> Text:
    text = Text(no_wrap=True)
    text.append("Only projects linked to this repository are shown.", style="dim")
    return text


def _build_item_text(item: ProjectPickerItem, name_column_width: int) -> Text:
    match item:
        case ProjectPickerProjectItem():
            return _build_project_item_text(item, name_column_width)
        case ProjectPickerCreateItem():
            return _build_action_item_text(
                "Create new project",
                item.label,
                name_column_width,
                name_style="cyan" if item.recommended else "",
            )
        case ProjectPickerLoadMoreItem():
            return _build_action_item_text(
                "Load more projects...", item.label, name_column_width
            )
        case ProjectPickerUnlinkItem():
            return _build_action_item_text(
                "Unlink project", item.label, name_column_width, name_style="red"
            )


def _build_project_item_text(
    item: ProjectPickerProjectItem, name_column_width: int
) -> Text:
    style = "bold" if item.recommended else ""
    return _build_three_column_text(
        name=item.project.name,
        repo_count=_repo_count_label(item.project),
        status=_project_status_label(item),
        name_column_width=name_column_width,
        name_style=style,
    )


def _build_action_item_text(
    name: str,
    label: str,
    name_column_width: int,
    *,
    name_style: str = "",
    label_style: str = "dim",
) -> Text:
    return _build_three_column_text(
        name=name,
        repo_count="",
        status=label,
        name_column_width=name_column_width,
        name_style=name_style,
        status_style=label_style,
    )


def _build_three_column_text(
    *,
    name: str,
    repo_count: str,
    status: str,
    name_column_width: int,
    name_style: str = "",
    repo_count_style: str = "dim",
    status_style: str = "dim",
) -> Text:
    text = Text(no_wrap=True)
    visible_name = _truncate_column_value(name, name_column_width)
    text.append(f"{visible_name:<{name_column_width}}", style=name_style)
    text.append(" " * _COLUMN_GAP)
    text.append(f"{repo_count:<{_REPO_COUNT_COLUMN_WIDTH}}", style=repo_count_style)
    if status:
        text.append(" " * _COLUMN_GAP)
        text.append(status, style=status_style)
    return text


def _repo_count_label(project: VibeCodeProject) -> str:
    count = len(project.repositories)
    noun = "repo" if count == 1 else "repos"
    return f"{count} {noun}"


def _project_status_label(item: ProjectPickerProjectItem) -> str:
    match item.match_kind:
        case ProjectMatchKind.CURRENT_LINK:
            return "Currently linked"
        case ProjectMatchKind.EXACT_REPO:
            return "Exact match found"
        case ProjectMatchKind.MULTI_REPO:
            return "Working repository found"


def _build_section_text(label: str) -> Text:
    text = Text(no_wrap=True)
    text.append(label, style="dim")
    return text


def _build_spacer_text() -> Text:
    return Text(" ", no_wrap=True)


def _truncate_column_value(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return f"{value[: width - 1]}…"


def _name_column_width(items: list[ProjectPickerItem]) -> int:
    names = [_item_name(item) for item in items]
    longest = max((len(name) for name in names), default=_MIN_NAME_COLUMN_WIDTH)
    return max(_MIN_NAME_COLUMN_WIDTH, min(longest, _MAX_NAME_COLUMN_WIDTH))


def _item_name(item: ProjectPickerItem) -> str:
    match item:
        case ProjectPickerProjectItem():
            return item.project.name
        case ProjectPickerCreateItem():
            return "Create new project"
        case ProjectPickerLoadMoreItem():
            return "Load more projects..."
        case ProjectPickerUnlinkItem():
            return "Unlink project"


class VibeCodeProjectPickerApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("/", "focus_search", "Search", show=False),
    ]

    class ProjectSelected(Message):
        def __init__(self, project_id: str, project_name: str) -> None:
            self.project_id = project_id
            self.project_name = project_name
            super().__init__()

    class CreateRequested(Message):
        def __init__(self, project_name: str) -> None:
            self.project_name = project_name
            super().__init__()

    class LoadMoreRequested(Message):
        pass

    class UnlinkRequested(Message):
        pass

    class Cancelled(Message):
        pass

    def __init__(
        self,
        *,
        context: ProjectPickerContext,
        projects: list[VibeCodeProject],
        has_more: bool = False,
        include_unlink: bool = False,
        title: str = "Select Vibe Code Web project",
        query: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(id="vibecodeprojectpicker-app", **kwargs)
        self._picker_context = context
        self._projects = projects
        self._has_more = has_more
        self._include_unlink = include_unlink
        self._title = title
        self._query = query
        self._items = self._build_items()

    @property
    def items(self) -> list[ProjectPickerItem]:
        return list(self._items)

    def update_projects(
        self, *, projects: list[VibeCodeProject], has_more: bool | None = None
    ) -> None:
        self._projects = projects
        if has_more is not None:
            self._has_more = has_more
        self._refresh_options()

    def focus_option(self, option_id: str) -> None:
        self._restore_highlight(option_id)

    def compose(self) -> ComposeResult:
        with Vertical(id="vibecodeprojectpicker-content"):
            yield NoMarkupStatic(self._title, classes="vibecodeprojectpicker-title")
            yield NoMarkupStatic(
                _build_repo_text(self._picker_context),
                classes="vibecodeprojectpicker-repo",
            )
            yield NoMarkupStatic(
                _build_scope_text(), classes="vibecodeprojectpicker-scope"
            )
            with Horizontal(classes="vibecodeprojectpicker-search-row"):
                yield NoMarkupStatic(
                    "Search projects:", classes="vibecodeprojectpicker-search-label"
                )
                yield VscodeCompatInput(
                    value=self._query, id="vibecodeprojectpicker-search", compact=True
                )
            yield NavigableOptionList(
                *self._option_list_items(), id="vibecodeprojectpicker-options"
            )
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Select  "
                    f"{shortcut('/')} Search  {shortcut('Esc')} Cancel"
                ),
                classes="vibecodeprojectpicker-help",
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._set_default_highlight()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "vibecodeprojectpicker-search":
            return
        self._query = event.value
        self._refresh_options()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.action_select()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        self._select_option_id(str(event.option.id))

    def on_key(self, event: events.Key) -> None:
        if not isinstance(self.screen.focused, Input):
            return
        match event.key:
            case "up":
                self._move_highlight(-1)
            case "down":
                self._move_highlight(1)
            case _:
                return
        event.prevent_default()
        event.stop()
        self.query_one(OptionList).focus()

    def action_focus_search(self) -> None:
        self.query_one(Input).focus()

    def action_select(self) -> None:
        option_id = self._highlighted_option_id()
        if option_id is None and self._items:
            option_id = self._items[0].option_id
        if option_id is None:
            return
        self._select_option_id(option_id)

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())

    def _build_items(self) -> list[ProjectPickerItem]:
        return build_project_picker_items(
            context=self._picker_context,
            projects=self._projects,
            query=self._query,
            has_more=self._has_more,
            include_unlink=self._include_unlink,
        )

    def _option_list_items(self) -> list[Option]:
        project_items = [
            item
            for item in self._items
            if isinstance(item, ProjectPickerProjectItem | ProjectPickerLoadMoreItem)
        ]
        action_items = [
            item
            for item in self._items
            if isinstance(item, ProjectPickerCreateItem | ProjectPickerUnlinkItem)
        ]
        name_column_width = _name_column_width(self._items)

        options: list[Option] = []
        if project_items:
            options.append(Option(_build_section_text("Projects"), disabled=True))
            for item in project_items:
                options.append(
                    Option(_build_item_text(item, name_column_width), id=item.option_id)
                )
        if action_items:
            options.append(Option(_build_spacer_text(), disabled=True))
            options.append(Option(_build_spacer_text(), disabled=True))
            options.append(Option(_build_section_text("Actions"), disabled=True))
            options.extend(
                Option(_build_item_text(item, name_column_width), id=item.option_id)
                for item in action_items
            )
        return options

    def _rendered_option_ids(self) -> list[str | None]:
        return [
            str(option.id) if option.id is not None else None
            for option in self._option_list_items()
        ]

    def _refresh_options(self) -> None:
        highlighted = self._highlighted_option_id()
        self._items = self._build_items()
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        option_list.add_options(self._option_list_items())
        self._restore_highlight(highlighted)

    def _highlighted_option_id(self) -> str | None:
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        return str(option.id)

    def _restore_highlight(self, option_id: str | None) -> None:
        option_list = self.query_one(OptionList)
        if not self._items:
            return
        if option_id is not None:
            for index, rendered_option_id in enumerate(self._rendered_option_ids()):
                if rendered_option_id == option_id:
                    option_list.highlighted = index
                    return
        self._set_default_highlight()

    def _set_default_highlight(self) -> None:
        rendered_option_ids = self._rendered_option_ids()
        if recommended_option_id := self._recommended_option_id():
            for index, option_id in enumerate(rendered_option_ids):
                if option_id == recommended_option_id:
                    self.query_one(OptionList).highlighted = index
                    return

        indexes = self._enabled_option_indexes()
        if indexes:
            self.query_one(OptionList).highlighted = indexes[0]

    def _move_highlight(self, delta: int) -> None:
        option_list = self.query_one(OptionList)
        enabled_indexes = self._enabled_option_indexes()
        if not enabled_indexes:
            return
        current = option_list.highlighted or enabled_indexes[0]
        try:
            current_enabled_index = enabled_indexes.index(current)
        except ValueError:
            current_enabled_index = 0
        option_list.highlighted = enabled_indexes[
            (current_enabled_index + delta) % len(enabled_indexes)
        ]

    def _enabled_option_indexes(self) -> list[int]:
        return [
            index
            for index, option_id in enumerate(self._rendered_option_ids())
            if option_id is not None
        ]

    def _recommended_option_id(self) -> str | None:
        item = next((item for item in self._items if item.recommended), None)
        if item is None:
            return None
        return item.option_id

    def _select_option_id(self, option_id: str) -> None:
        item = next((item for item in self._items if item.option_id == option_id), None)
        if item is None:
            return

        match item:
            case ProjectPickerProjectItem():
                self.post_message(
                    self.ProjectSelected(
                        project_id=item.project.project_id,
                        project_name=item.project.name,
                    )
                )
            case ProjectPickerCreateItem():
                self.post_message(self.CreateRequested(item.project_name))
            case ProjectPickerLoadMoreItem():
                self.post_message(self.LoadMoreRequested())
            case ProjectPickerUnlinkItem():
                self.post_message(self.UnlinkRequested())
