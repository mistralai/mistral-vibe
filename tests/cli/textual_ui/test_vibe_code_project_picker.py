from __future__ import annotations

from pathlib import Path
from typing import cast

from rich.text import Text
from textual.widgets import OptionList

from vibe.cli.textual_ui.widgets.vibe_code_project import (
    VibeCodeProjectCreateApp,
    VibeCodeProjectPickerApp,
)
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    ProjectRepository,
    VibeCodeProject,
)

CURRENT_REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


def _context() -> ProjectPickerContext:
    return ProjectPickerContext(
        repo_root=Path("/repo/mistral-vibe"),
        repo_url=CURRENT_REPO_URL,
        repo_name="mistral-vibe",
    )


def _project(project_id: str, name: str, *repo_urls: str) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=tuple(ProjectRepository(repo_url=url) for url in repo_urls),
    )


class TestVibeCodeProjectPickerAppInit:
    def test_id_is_vibecodeprojectpicker_app(self) -> None:
        picker = VibeCodeProjectPickerApp(context=_context(), projects=[])

        assert picker.id == "vibecodeprojectpicker-app"

    def test_marks_create_recommended_when_no_projects_match(self) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[
                _project(
                    "tools", "Internal Tools", "https://github.com/mistralai/tools"
                )
            ],
        )

        assert [item.option_id for item in picker.items] == ["action:create"]
        assert picker.items[0].recommended is True

    def test_rendered_options_separate_actions(self) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
        )

        option_ids = [
            str(option.id) if option.id is not None else None
            for option in picker._option_list_items()
        ]

        assert option_ids == [
            None,
            "project:mistral-vibe",
            None,
            None,
            None,
            "action:create",
        ]

        option_prompts = {
            str(option.id): cast(Text, option.prompt).plain
            for option in picker._option_list_items()
            if option.id is not None
        }
        assert "1 repo" in option_prompts["project:mistral-vibe"]
        assert "Exact match found" in option_prompts["project:mistral-vibe"]
        assert "Create new project" in option_prompts["action:create"]
        assert "mistral-vibe" not in option_prompts["action:create"]

    def test_project_rows_use_three_aligned_columns(self) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[
                _project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL),
                _project(
                    "multi",
                    "Mistral Vibe + Docs",
                    CURRENT_REPO_URL,
                    "https://github.com/mistralai/docs.git",
                    "https://github.com/mistralai/examples.git",
                ),
            ],
        )

        option_prompts = {
            str(option.id): cast(Text, option.prompt).plain
            for option in picker._option_list_items()
            if option.id is not None
        }

        assert "1 repo" in option_prompts["project:mistral-vibe"]
        assert "Exact match found" in option_prompts["project:mistral-vibe"]
        assert "3 repos" in option_prompts["project:multi"]
        assert "Working repository found" in option_prompts["project:multi"]
        assert option_prompts["project:mistral-vibe"].index("1 repo") == (
            option_prompts["project:multi"].index("3 repos")
        )
        assert option_prompts["project:mistral-vibe"].index("Exact match found") == (
            option_prompts["project:multi"].index("Working repository found")
        )


class TestVibeCodeProjectPickerMessages:
    def test_project_selected_stores_project_info(self) -> None:
        message = VibeCodeProjectPickerApp.ProjectSelected("project-id", "Mistral Vibe")

        assert message.project_id == "project-id"
        assert message.project_name == "Mistral Vibe"

    def test_create_requested_stores_project_name(self) -> None:
        message = VibeCodeProjectPickerApp.CreateRequested("mistral-vibe")

        assert message.project_name == "mistral-vibe"

    def test_create_submitted_stores_project_name_and_default_branch(self) -> None:
        message = VibeCodeProjectCreateApp.Submitted("Mistral Vibe", "main")

        assert message.project_name == "Mistral Vibe"
        assert message.default_branch == "main"

    def test_action_select_posts_project_selection(self, monkeypatch) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
        )
        option_list = FakeOptionList([item.option_id for item in picker.items])
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_select()

        message = posted_messages[0]
        assert isinstance(message, VibeCodeProjectPickerApp.ProjectSelected)
        assert message.project_id == "mistral-vibe"
        assert message.project_name == "Mistral Vibe"

    def test_action_select_posts_create_request_for_create_item(
        self, monkeypatch
    ) -> None:
        picker = VibeCodeProjectPickerApp(context=_context(), projects=[])
        option_list = FakeOptionList([item.option_id for item in picker.items])
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_select()

        message = posted_messages[0]
        assert isinstance(message, VibeCodeProjectPickerApp.CreateRequested)
        assert message.project_name == "mistral-vibe"

    def test_selecting_load_more_posts_load_more_request(self, monkeypatch) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
            has_more=True,
        )
        option_list = FakeOptionList([item.option_id for item in picker.items])
        option_list.highlighted = option_list.option_ids.index("action:load_more")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_select()

        assert isinstance(
            posted_messages[0], VibeCodeProjectPickerApp.LoadMoreRequested
        )

    def test_selecting_unlink_posts_unlink_request(self, monkeypatch) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
            include_unlink=True,
        )
        option_list = FakeOptionList([item.option_id for item in picker.items])
        option_list.highlighted = len(picker.items) - 1
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_select()

        assert isinstance(posted_messages[0], VibeCodeProjectPickerApp.UnlinkRequested)

    def test_option_selected_uses_event_option_id(self, monkeypatch) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
        )
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.on_option_list_option_selected(
            cast(OptionList.OptionSelected, FakeOptionEvent("project:mistral-vibe"))
        )

        assert isinstance(posted_messages[0], VibeCodeProjectPickerApp.ProjectSelected)

    def test_non_exact_project_selects_directly(self, monkeypatch) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[
                _project(
                    "multi",
                    "Mistral Vibe + Docs",
                    CURRENT_REPO_URL,
                    "https://github.com/mistralai/docs.git",
                )
            ],
        )
        option_list = FakeOptionList([item.option_id for item in picker.items])
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_select()

        assert isinstance(posted_messages[0], VibeCodeProjectPickerApp.ProjectSelected)
        assert posted_messages[0].project_id == "multi"
        assert posted_messages[0].project_name == "Mistral Vibe + Docs"


class TestVibeCodeProjectPickerUpdates:
    def test_update_projects_rebuilds_options(self, monkeypatch) -> None:
        picker = VibeCodeProjectPickerApp(
            context=_context(),
            projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
            has_more=True,
        )
        option_list = FakeOptionList([item.option_id for item in picker.items])
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)

        picker.update_projects(
            projects=[
                _project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL),
                _project("docs", "Docs", CURRENT_REPO_URL),
            ],
            has_more=False,
        )

        assert option_list.cleared is True
        assert option_list.added_option_ids == [
            "project:docs",
            "project:mistral-vibe",
            "action:create",
        ]


class FakeOption:
    def __init__(self, option_id: str) -> None:
        self.id = option_id


class FakeOptionEvent:
    def __init__(self, option_id: str) -> None:
        self.option = FakeOption(option_id)


class FakeOptionList:
    def __init__(self, option_ids: list[str]) -> None:
        self.option_ids = option_ids
        self.rendered_option_ids: list[str | None] = list(option_ids)
        self.highlighted = 0
        self.cleared = False
        self.added_option_ids: list[str] = []
        self.replaced_prompts: dict[str, str] = {}
        self.focused = False

    @property
    def highlighted_option(self) -> FakeOption | None:
        if not self.rendered_option_ids:
            return None
        option_id = self.rendered_option_ids[self.highlighted]
        if option_id is None:
            return None
        return FakeOption(option_id)

    def clear_options(self) -> None:
        self.cleared = True
        self.option_ids = []

    def add_options(self, options: list[object]) -> None:
        self.added_option_ids = []
        self.rendered_option_ids = []
        for option in options:
            option_id = getattr(option, "id", None)
            self.rendered_option_ids.append(
                str(option_id) if option_id is not None else None
            )
            if option_id is not None:
                self.added_option_ids.append(str(option_id))
                prompt = getattr(option, "prompt", None)
                self.replaced_prompts[str(option_id)] = cast(Text, prompt).plain
        self.option_ids = self.added_option_ids

    def focus(self) -> None:
        self.focused = True
