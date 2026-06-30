from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

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
from textual.widgets import Static

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.paths import TRUSTED_FOLDERS_FILE
from vibe.core.trusted_folders import WorkspaceTrustDecision


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
            self.decision = decision

    def __init__(
        self,
        cwd: Path,
        repo_root: Path | None,
        detected_files: list[str],
        repo_detected_files: list[str] | None = None,
        offer_repo_trust: bool = False,
        repo_explicitly_untrusted: bool = False,
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
        self._options: list[tuple[TrustDecision, str]] = self._build_options()
        # Default to the safest option (rightmost: Decline).
        self.selected_option = len(self._options) - 1
        self.option_widgets: list[Static] = []

    @property
    def _title(self) -> str:
        if self.offer_repo_trust:
            return "Trust folder or repository?"
        return "Trust this folder?"

    def _build_options(self) -> list[tuple[TrustDecision, str]]:
        options: list[tuple[TrustDecision, str]] = []
        if self.offer_repo_trust:
            options.append((TrustDecision.TRUST_REPO, "Trust full repo"))
        options.append((TrustDecision.TRUST_CWD, "Trust folder"))
        options.append((TrustDecision.DECLINE, "Don't trust"))
        return options

    def _compose_scroll_content(self) -> ComposeResult:
        why_content = (
            "Files here can modify AI behavior. Malicious "
            "configs may exfiltrate data, run destructive "
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
                yield NoMarkupStatic(self._title, id="trust-dialog-title")
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

                with VerticalScroll(id="trust-dialog-content"):
                    yield from self._compose_scroll_content()

                yield NoMarkupStatic(
                    "Only trust folders you fully control",
                    id="trust-dialog-footer-warning",
                    classes="trust-dialog-footer-warning",
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
                    f"Setting will be saved in: {TRUSTED_FOLDERS_FILE.path}",
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


class TrustFolderApp(App):
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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.cwd = cwd
        self.repo_root = repo_root
        self.offer_repo_trust = offer_repo_trust
        self.repo_explicitly_untrusted = repo_explicitly_untrusted
        self.detected_files = detected_files
        self.repo_detected_files = repo_detected_files or []
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
        )

    def action_quit_without_saving(self) -> None:
        self._quit_without_saving = True
        self.exit()

    def on_trust_folder_dialog_decided(
        self, message: TrustFolderDialog.Decided
    ) -> None:
        self._result = message.decision
        self.exit()

    def run_trust_dialog(self) -> TrustDecision | None:
        self.run(inline=True)
        if self._quit_without_saving:
            raise TrustDialogQuitException()
        return self._result


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
