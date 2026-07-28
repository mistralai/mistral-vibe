from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, cast

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import (
    Center,
    CenterMiddle,
    Horizontal,
    Vertical,
    VerticalScroll,
)
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from vibe.app_server.models import WorkspaceTrustDecision, WorkspaceTrustDetails
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


class TrustDialogQuitException(Exception):
    pass


TrustDecision = WorkspaceTrustDecision


class TrustFolderDialog(CenterMiddle):
    can_focus = True
    can_focus_children = True

    # Number keys 1-3 cover up to three options; extras no-op.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_index(0)", show=False),
        Binding("2", "select_index(1)", show=False),
        Binding("3", "select_index(2)", show=False),
    ]

    class Decided(Message):
        def __init__(self, decision: TrustDecision) -> None:
            super().__init__()
            self.decision: TrustDecision = decision

    def __init__(
        self,
        cwd: Path,
        repo_root: Path | None,
        detected_files: list[str],
        repo_detected_files: list[str] | None = None,
        offer_repo_trust: bool = False,
        repo_explicitly_untrusted: bool = False,
        settings_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.cwd = cwd
        # Hide the repo line when it would duplicate cwd.
        self.repo_root = repo_root if repo_root and repo_root != cwd else None
        self.offer_repo_trust = offer_repo_trust and self.repo_root is not None
        self.repo_explicitly_untrusted = (
            repo_explicitly_untrusted and self.repo_root is not None
        )
        self.detected_files = detected_files
        self.repo_detected_files = repo_detected_files or []
        self.settings_path = settings_path
        self._options: list[tuple[TrustDecision, str]] = self._build_options()
        # Default to "Trust folder" (trust_cwd) when available.
        self.selected_option = next(
            (i for i, (d, _) in enumerate(self._options) if d == "trust_cwd"), 0
        )
        self.option_widgets: list[Static] = []

    @property
    def _title(self) -> str:
        if self.offer_repo_trust:
            return "Trust folder or repository?"
        return "Trust this folder?"

    def _build_options(self) -> list[tuple[TrustDecision, str]]:
        options: list[tuple[TrustDecision, str]] = []
        if self.offer_repo_trust:
            options.append(("trust_repo", "Trust full repo"))
        options.append(("trust_cwd", "Trust folder"))
        options.append(("decline", "Don't trust"))
        return options

    def _compose_scroll_content(self) -> ComposeResult:
        why_content = (
            "Malicious configs can modify AI behavior, exfiltrate data, run destructive "
            "commands, or silently alter your code."
        )
        with Center(classes="trust-dialog-section-center"):
            yield NoMarkupStatic(
                why_content,
                id="trust-dialog-warning",
                classes="trust-dialog-section-content",
            )

        if self.detected_files:
            with Center(classes="trust-dialog-section-center"):
                with Vertical(classes="trust-dialog-section-stack"):
                    yield NoMarkupStatic(
                        "Detected in current folder:",
                        classes="trust-dialog-section-title",
                    )
                    yield NoMarkupStatic(
                        "\n".join(f"\u2022 {f}" for f in self.detected_files),
                        id="trust-dialog-files",
                        classes="trust-dialog-section-content trust-dialog-file-list",
                    )

        if self.repo_detected_files:
            with Center(classes="trust-dialog-section-center"):
                with Vertical(classes="trust-dialog-section-stack"):
                    yield NoMarkupStatic(
                        "Detected in repository context:",
                        classes="trust-dialog-section-title",
                    )
                    yield NoMarkupStatic(
                        "\n".join(f"\u2022 {f}" for f in self.repo_detected_files),
                        id="trust-dialog-files-repo",
                        classes="trust-dialog-section-content trust-dialog-file-list",
                    )

    def compose(self) -> ComposeResult:
        with CenterMiddle(id="trust-dialog-container"):
            with CenterMiddle(id="trust-dialog"):
                with VerticalScroll(id="trust-dialog-content"):
                    yield from self._compose_scroll_content()

                yield NoMarkupStatic(
                    self._title,
                    id="trust-dialog-footer-warning",
                    classes="trust-dialog-footer-warning",
                )

                path_classes = "trust-dialog-path"
                if self.repo_root is not None:
                    path_classes += " has-repo-root"
                yield NoMarkupStatic(
                    str(self.cwd), id="trust-dialog-path", classes=path_classes
                )
                if self.repo_explicitly_untrusted:
                    yield NoMarkupStatic(
                        f"\u26a0 git repository {self.repo_root} is marked untrusted",
                        id="trust-dialog-repo-untrusted",
                        classes="trust-dialog-repo-untrusted",
                    )
                elif self.repo_root is not None:
                    yield NoMarkupStatic(
                        f"\u21b3 git repository: {self.repo_root}",
                        id="trust-dialog-repo-root",
                        classes="trust-dialog-repo-root",
                    )

                with Horizontal(id="trust-options-container"):
                    for idx, (_decision, label) in enumerate(self._options):
                        widget = NoMarkupStatic(
                            f"  {idx + 1}. {label}", classes="trust-option"
                        )
                        self.option_widgets.append(widget)
                        yield widget

                yield NoMarkupStatic(
                    shortcut_hint(
                        f"{shortcut('←→')} navigate  {shortcut('Enter')} select"
                    ),
                    classes="trust-dialog-help",
                )

                yield NoMarkupStatic(
                    (
                        f"Setting will be saved in: {self.settings_path}"
                        if self.settings_path is not None
                        else "Setting will be saved in Vibe's trusted folder settings"
                    ),
                    id="trust-dialog-save-info",
                    classes="trust-dialog-save-info",
                )

    async def on_mount(self) -> None:
        self._update_options()
        self.focus()

    def _update_options(self) -> None:
        if len(self.option_widgets) != len(self._options):
            return

        for idx, ((_, label), widget) in enumerate(
            zip(self._options, self.option_widgets, strict=True)
        ):
            is_selected = idx == self.selected_option

            cursor = "› " if is_selected else "  "
            widget.update(f"{cursor}{label}")

            widget.remove_class("trust-cursor-selected")
            widget.remove_class("trust-option-selected")

            if is_selected:
                widget.add_class("trust-cursor-selected")
            else:
                widget.add_class("trust-option-selected")

    def action_move_left(self) -> None:
        self.selected_option = (self.selected_option - 1) % len(self._options)
        self._update_options()

    def action_move_right(self) -> None:
        self.selected_option = (self.selected_option + 1) % len(self._options)
        self._update_options()

    def action_select(self) -> None:
        self._handle_selection(self.selected_option)

    def action_select_index(self, idx: int) -> None:
        if not 0 <= idx < len(self._options):
            return
        self.selected_option = idx
        self._handle_selection(idx)

    def _handle_selection(self, option: int) -> None:
        decision, _ = self._options[option]
        self.post_message(self.Decided(decision))

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)


class TrustFolderApp(App[TrustDecision | None]):
    CSS_PATH = "trust_folder_dialog.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit_without_saving", "Quit", show=False, priority=True),
        Binding("ctrl+c", "quit_without_saving", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        cwd: Path,
        repo_root: Path | None,
        detected_files: list[str],
        repo_detected_files: list[str] | None = None,
        offer_repo_trust: bool = False,
        repo_explicitly_untrusted: bool = False,
        settings_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.cwd = cwd
        self.repo_root = repo_root
        self.offer_repo_trust = offer_repo_trust
        self.repo_explicitly_untrusted = repo_explicitly_untrusted
        self.detected_files = detected_files
        self.repo_detected_files = repo_detected_files or []
        self.settings_path = settings_path
        self._result: TrustDecision | None = None
        self._quit_without_saving = False

    def on_mount(self) -> None:
        self.theme = "ansi-dark"

    def compose(self) -> ComposeResult:
        yield TrustFolderDialog(
            self.cwd,
            self.repo_root,
            self.detected_files,
            repo_detected_files=self.repo_detected_files,
            offer_repo_trust=self.offer_repo_trust,
            repo_explicitly_untrusted=self.repo_explicitly_untrusted,
            settings_path=self.settings_path,
        )

    def action_quit_without_saving(self) -> None:
        self._quit_without_saving = True
        self.exit(result=None)

    def on_trust_folder_dialog_decided(
        self, message: TrustFolderDialog.Decided
    ) -> None:
        self._result = cast(TrustDecision, message.decision)
        self.exit(result=self._result)

    def run_trust_dialog(self) -> TrustDecision | None:
        result = self.run(inline=True)
        if self._quit_without_saving:
            raise TrustDialogQuitException()
        return result

    async def run_trust_dialog_async(self) -> TrustDecision | None:
        result = await self.run_async(inline=True)
        if self._quit_without_saving:
            raise TrustDialogQuitException()
        return result


class TrustFolderScreen(ModalScreen[TrustDecision | None]):
    CSS_PATH = "trust_folder_dialog.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit_without_saving", show=False, priority=True),
        Binding("ctrl+c", "quit_without_saving", show=False, priority=True),
    ]

    def __init__(self, details: WorkspaceTrustDetails) -> None:
        super().__init__()
        self._details = details

    def compose(self) -> ComposeResult:
        details = self._details
        yield TrustFolderDialog(
            Path(details.cwd),
            Path(details.repo_root) if details.repo_root is not None else None,
            details.detected_files,
            repo_detected_files=details.repo_detected_files,
            offer_repo_trust="trust_repo" in details.available_decisions,
            repo_explicitly_untrusted=details.repo_explicitly_untrusted,
            settings_path=details.settings_path,
        )

    def action_quit_without_saving(self) -> None:
        self.dismiss(None)

    def on_trust_folder_dialog_decided(
        self, message: TrustFolderDialog.Decided
    ) -> None:
        self.dismiss(cast(TrustDecision, message.decision))


def ask_trust_folder(
    cwd: Path,
    repo_root: Path | None,
    detected_files: list[str],
    repo_detected_files: list[str] | None = None,
    offer_repo_trust: bool = False,
    repo_explicitly_untrusted: bool = False,
) -> TrustDecision | None:
    app = TrustFolderApp(
        cwd,
        repo_root,
        detected_files,
        repo_detected_files=repo_detected_files,
        offer_repo_trust=offer_repo_trust,
        repo_explicitly_untrusted=repo_explicitly_untrusted,
    )
    return app.run_trust_dialog()
