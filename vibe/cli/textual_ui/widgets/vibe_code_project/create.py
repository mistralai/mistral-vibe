from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.message import Message

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput


class VibeCodeProjectCreateApp(Vertical):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class Submitted(Message):
        def __init__(self, project_name: str, default_branch: str) -> None:
            self.project_name = project_name
            self.default_branch = default_branch
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(
        self, *, project_name: str, repo_label: str, default_branch: str, **kwargs: Any
    ) -> None:
        super().__init__(id="vibecodeprojectcreate-app", **kwargs)
        self._project_name = project_name
        self._repo_label = repo_label
        self._default_branch = default_branch

    def compose(self) -> ComposeResult:
        with Vertical(id="vibecodeprojectcreate-content"):
            yield NoMarkupStatic(
                "Create Vibe Code Web project", classes="vibecodeprojectcreate-title"
            )
            yield NoMarkupStatic(
                f"Repository: {self._repo_label}", classes="vibecodeprojectcreate-repo"
            )
            with Horizontal(classes="vibecodeprojectcreate-input-row"):
                yield NoMarkupStatic(
                    "Project name:", classes="vibecodeprojectcreate-input-label"
                )
                yield VscodeCompatInput(
                    value=self._project_name,
                    id="vibecodeprojectcreate-name",
                    compact=True,
                )
            with Horizontal(classes="vibecodeprojectcreate-input-row"):
                yield NoMarkupStatic(
                    "Default branch:", classes="vibecodeprojectcreate-input-label"
                )
                yield VscodeCompatInput(
                    value=self._default_branch,
                    id="vibecodeprojectcreate-default-branch",
                    compact=True,
                )
            yield NoMarkupStatic(
                shortcut_hint(f"{shortcut('Enter')} Create  {shortcut('Esc')} Back"),
                classes="vibecodeprojectcreate-help",
            )

    def on_mount(self) -> None:
        self.query_one(VscodeCompatInput).focus()

    def on_input_submitted(self, event: VscodeCompatInput.Submitted) -> None:
        if event.input.id not in {
            "vibecodeprojectcreate-name",
            "vibecodeprojectcreate-default-branch",
        }:
            return
        self._submit()

    def _submit(self) -> None:
        project_name = self.query_one(
            "#vibecodeprojectcreate-name", VscodeCompatInput
        ).value.strip()
        default_branch = self.query_one(
            "#vibecodeprojectcreate-default-branch", VscodeCompatInput
        ).value.strip()
        if not project_name or not default_branch:
            return
        self.post_message(self.Submitted(project_name, default_branch))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
