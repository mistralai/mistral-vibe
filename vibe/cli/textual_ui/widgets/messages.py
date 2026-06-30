from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from vibe.core.hooks.models import HookMessageSeverity
from vibe.core.logger import logger
from vibe.core.types import FileImageSource, ImageAttachment, InlineImageSource
from vibe.core.utils.io import read_safe_async

if TYPE_CHECKING:
    from vibe.cli.textual_ui.app import ChatScroll


from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Link, Markdown, Static
from textual.widgets._markdown import MarkdownStream
from watchfiles import awatch

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.collapsible import (
    ClickWithoutDragMixin,
    CollapsibleSection,
    lines_label,
)
from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)
from vibe.cli.textual_ui.widgets.spinner import SpinnerMixin, SpinnerType


class ExpandingBorder(NonSelectableStatic):
    def __init__(self, *, classes: str | None = None) -> None:
        super().__init__(classes=classes)
        self._row_colors: dict[int, str] = {}

    def set_row_colors(self, colors: dict[int, str]) -> None:
        self._row_colors = colors
        self.refresh()

    def render(self) -> Content | str:
        height = self.size.height
        chars = ["⎢"] * (height - 1) + ["⎣"]
        if not self._row_colors:
            return "\n".join(chars)

        rendered = Content("")
        for i, ch in enumerate(chars):
            if i > 0:
                rendered += Content("\n")
            if color := self._row_colors.get(i):
                rendered += Content.styled(ch, color)
            else:
                rendered += Content(ch)
        return rendered

    def on_resize(self) -> None:
        self.refresh()


# Mimic a border bottom with this component in order to have dimmed colors in ANSI themes
# Move back to border when Textual supports dimmed borders or foreground-muted in ANSI themes
class ExpandingSeparator(NonSelectableStatic):
    def render(self) -> str:
        return "─" * max(self.size.width, 1)

    def on_resize(self) -> None:
        self.refresh()


def _attachment_label(attachment: ImageAttachment) -> str:
    alias_path = Path(attachment.alias).expanduser()
    if not alias_path.is_absolute():
        return attachment.alias
    return _format_display_path(alias_path)


def _format_display_path(path: Path) -> str:
    home = Path.home()
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    if str(relative) == ".":
        return "~"
    return f"~/{relative}"


class UserMessageAttachment(Horizontal):
    def __init__(self, attachment: ImageAttachment) -> None:
        super().__init__(classes="user-message-attachment-line")
        self._attachment = attachment

    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(
            "└ attached image: ", classes="user-message-attachment-label"
        )
        match self._attachment.source:
            case FileImageSource(path=path):
                yield Link(
                    _attachment_label(self._attachment),
                    url=path.as_uri(),
                    classes="user-message-attachment-link",
                )
            case InlineImageSource():
                # Inline images have no file on disk, so there's nothing to link.
                yield NoMarkupStatic(
                    _attachment_label(self._attachment),
                    classes="user-message-attachment-link",
                )


class UserMessage(Static):
    PROMPT_CHAR: ClassVar[str] = ">"
    SHOW_SEPARATOR: ClassVar[bool] = True

    def __init__(
        self,
        content: str,
        pending: bool = False,
        message_index: int | None = None,
        images: list[ImageAttachment] | None = None,
    ) -> None:
        super().__init__()
        self.add_class("user-message")
        self._content = content
        self._pending = pending
        self._images = images or []
        self.message_index: int | None = message_index

    def get_content(self) -> str:
        return self._content

    @property
    def pending(self) -> bool:
        return self._pending

    def compose(self) -> ComposeResult:
        with Vertical(classes="user-message-wrapper"):
            with Horizontal(classes="user-message-container"):
                yield NonSelectableStatic(
                    f"{self.PROMPT_CHAR} ", classes="user-message-prompt"
                )
                yield NoMarkupStatic(self._content, classes="user-message-content")
            if self._images:
                with Vertical(classes="user-message-attachments"):
                    for image in self._images:
                        yield UserMessageAttachment(image)
            if self.SHOW_SEPARATOR:
                yield ExpandingSeparator(classes="user-message-separator")
            if self._pending:
                self.add_class("pending")

    @staticmethod
    def _attachment_label(attachment: ImageAttachment) -> str:
        return _attachment_label(attachment)

    @staticmethod
    def _format_display_path(path: Path) -> str:
        return _format_display_path(path)

    async def set_pending(self, pending: bool) -> None:
        if pending == self._pending:
            return

        self._pending = pending

        if pending:
            self.add_class("pending")
            return

        self.remove_class("pending")

    def set_show_separator(self, show: bool) -> None:
        self.set_class(not show, "no-separator")

    def set_follows_previous(self, follows: bool) -> None:
        self.set_class(follows, "follows-user")


class QueueHeaderMessage(Static):
    DEFAULT_LABEL = "» Queued"
    PAUSED_LABEL = f"» Queued — press {shortcut('Enter')} to send, type to add"

    def __init__(self, *, paused: bool = False) -> None:
        super().__init__()
        self.add_class("queue-header-message")
        self._paused = paused
        self._label_widget: NoMarkupStatic | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="queue-header-container"):
            self._label_widget = NoMarkupStatic(
                shortcut_hint(self._current_label()), classes="queue-header-content"
            )
            yield self._label_widget
            yield ExpandingSeparator(classes="queue-header-separator")

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        if self._label_widget is not None:
            self._label_widget.update(shortcut_hint(self._current_label()))

    def _current_label(self) -> str:
        return self.PAUSED_LABEL if self._paused else self.DEFAULT_LABEL


class SlashCommandMessage(UserMessage):
    PROMPT_CHAR = "/"
    SHOW_SEPARATOR = False

    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.add_class("slash-command-message")


class TeleportUserMessage(UserMessage):
    PROMPT_CHAR = "&"


class StreamingMessageBase(Static):
    def __init__(self, content: str) -> None:
        super().__init__()
        self._content = content
        self._markdown: Markdown | None = None
        self._stream: MarkdownStream | None = None
        self._content_initialized = False
        self._to_write_buffer = ""

    def _get_markdown(self) -> Markdown:
        if self._markdown is None:
            raise RuntimeError(
                "Markdown widget not initialized. compose() must be called first."
            )
        return self._markdown

    def _ensure_stream(self) -> MarkdownStream:
        if self._stream is None:
            self._stream = Markdown.get_stream(self._get_markdown())
        return self._stream

    def _is_chat_at_bottom(self) -> bool:
        try:
            chat = cast("ChatScroll", self.app.query_one("#chat"))
            return chat.is_at_bottom
        except Exception:
            return True

    async def append_content(self, content: str) -> None:
        if not content:
            return

        self._content += content

        if not self._should_write_content():
            return

        if self._is_chat_at_bottom():
            to_write = self._to_write_buffer + content
            self._to_write_buffer = ""
            stream = self._ensure_stream()
            await stream.write(to_write)
            return

        self._to_write_buffer += content

    async def write_initial_content(self) -> None:
        if self._content_initialized:
            return
        self._content_initialized = True
        if self._content and self._should_write_content():
            stream = self._ensure_stream()
            await stream.write(self._content)
            self._to_write_buffer = ""

    async def stop_stream(self) -> None:
        if self._to_write_buffer and self._should_write_content():
            stream = self._ensure_stream()
            await stream.write(self._to_write_buffer)
        self._to_write_buffer = ""

        if self._stream is None:
            return

        await self._stream.stop()
        self._stream = None

    def _should_write_content(self) -> bool:
        return True

    def get_content(self) -> str:
        return self._content

    def is_stripped_content_empty(self) -> bool:
        return self._content.strip() == ""


class AssistantMessage(StreamingMessageBase):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.add_class("assistant-message")

    def compose(self) -> ComposeResult:
        markdown = Markdown("")
        self._markdown = markdown
        yield markdown


class ReasoningMessage(ClickWithoutDragMixin, SpinnerMixin, StreamingMessageBase):
    SPINNER_TYPE = SpinnerType.PULSE
    SPINNING_TEXT = "Thinking"
    COMPLETED_TEXT = "Thought"

    def __init__(self, content: str, collapsed: bool = True) -> None:
        super().__init__(content)
        self.add_class("reasoning-message")
        self.collapsed = collapsed
        self._indicator_widget: Static | None = None
        self._triangle_widget: Static | None = None
        self._header_widget: Horizontal | None = None
        self.init_spinner()

    def compose(self) -> ComposeResult:
        with Vertical(classes="reasoning-message-wrapper"):
            self._header_widget = Horizontal(classes="reasoning-message-header")
            with self._header_widget:
                self._indicator_widget = NonSelectableStatic(
                    self._spinner.current_frame(), classes="reasoning-indicator"
                )
                yield self._indicator_widget
                self._status_text_widget = NoMarkupStatic(
                    self.SPINNING_TEXT, classes="reasoning-collapsed-text"
                )
                yield self._status_text_widget
                self._triangle_widget = NonSelectableStatic(
                    "▶" if self.collapsed else "▼", classes="reasoning-triangle"
                )
                yield self._triangle_widget
            markdown = Markdown("", classes="reasoning-message-content")
            markdown.display = not self.collapsed
            self._markdown = markdown
            yield markdown

    def on_mount(self) -> None:
        self.start_spinner_timer()

    def on_resize(self) -> None:
        self.refresh_spinner()

    def stop_spinning(self, success: bool = True) -> None:
        super().stop_spinning(success)
        if self._indicator_widget:
            self._indicator_widget.update("■")

    def _is_click_on_toggle(self, event: events.Click) -> bool:
        return self._is_click_within(event, self._header_widget)

    async def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        await self._toggle_collapsed()

    async def _toggle_collapsed(self) -> None:
        await self.set_collapsed(not self.collapsed)

    def _should_write_content(self) -> bool:
        return not self.collapsed

    async def set_collapsed(self, collapsed: bool) -> None:
        if self.collapsed == collapsed:
            return

        self.collapsed = collapsed
        if self._triangle_widget:
            self._triangle_widget.update("▶" if collapsed else "▼")
        if self._markdown:
            self._markdown.display = not collapsed
            if not collapsed and self._content:
                if self._stream is not None:
                    await self._stream.stop()
                    self._stream = None
                await self._markdown.update("")
                stream = self._ensure_stream()
                await stream.write(self._content)
                self._to_write_buffer = ""


class UserCommandMessage(Static):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.add_class("user-command-message")
        self._content = content

    def compose(self) -> ComposeResult:
        with Horizontal(classes="user-command-container"):
            yield ExpandingBorder(classes="user-command-border")
            with Vertical(classes="user-command-content"):
                yield Markdown(self._content)


VSCODE_EXTENSION_URI = "vscode:extension/mistralai.mistral-vibe-code"
VSCODE_EXTENSION_LINK_LABEL = "VS Code extension"
VSCODE_EXTENSION_PROMO_STANDALONE = f"We now have a [{VSCODE_EXTENSION_LINK_LABEL}]({VSCODE_EXTENSION_URI}) with a rich UI. Check it out!"
VSCODE_EXTENSION_PROMO_WHATS_NEW_SUFFIX = (
    f"\n\n_Btw, we also have a new [{VSCODE_EXTENSION_LINK_LABEL}]"
    f"({VSCODE_EXTENSION_URI}). Check it out!_"
)


class WhatsNewMessage(Static):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.add_class("whats-new-message")
        self._content = content

    def compose(self) -> ComposeResult:
        yield Markdown(self._content)


class VscodeExtensionPromoMessage(Static):
    def __init__(self, content: str = VSCODE_EXTENSION_PROMO_STANDALONE) -> None:
        super().__init__()
        self.add_class("vscode-extension-promo-message")
        self._content = content

    def compose(self) -> ComposeResult:
        yield Markdown(self._content)


class InterruptMessage(Static):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("interrupt-message")

    def compose(self) -> ComposeResult:
        with Horizontal(classes="interrupt-container"):
            yield ExpandingBorder(classes="interrupt-border")
            yield NoMarkupStatic(
                "Interrupted · What should Vibe do instead?",
                classes="interrupt-content",
            )


class BashOutputMessage(ClickWithoutDragMixin, SpinnerMixin, Static):
    SPINNER_TYPE = SpinnerType.PULSE
    PREVIEW_LINES = 20

    def __init__(
        self,
        command: str,
        cwd: str,
        output: str = "",
        exit_code: int = 0,
        *,
        pending: bool = False,
    ) -> None:
        super().__init__()
        self.init_spinner()
        self.add_class("bash-output-message")
        self._command = command
        self._cwd = cwd
        self._output = output.rstrip("\n")
        self._exit_code = exit_code
        self._pending = pending
        self._queued = False
        self._output_widget: NoMarkupStatic | None = None
        self._overflow_widget: NoMarkupStatic | None = None
        self._section: CollapsibleSection | None = None
        self._output_container: Horizontal | None = None
        self._prompt_widget: NonSelectableStatic | None = None
        self._indicator_widget: Static | None = None

    QUEUED_PROMPT = "! "

    def _preview_text(self) -> str:
        return "\n".join(self._output.splitlines()[: self.PREVIEW_LINES])

    def _overflow_text(self) -> str:
        return "\n".join(self._output.splitlines()[self.PREVIEW_LINES :])

    def _overflow_count(self) -> int:
        return max(0, len(self._output.splitlines()) - self.PREVIEW_LINES)

    def _refresh_output_widgets(self) -> None:
        count = self._overflow_count()
        if self._output_widget:
            self._output_widget.update(self._preview_text())
        if self._overflow_widget:
            self._overflow_widget.update(self._overflow_text())
        if self._section:
            self._section.display = count > 0
            self._section.set_collapsed_label(lines_label(count, prefix="+"))

    def _update_spinner_frame(self) -> None:
        if not self._is_spinning or not self._prompt_widget or self._queued:
            return
        self._prompt_widget.update(f"{self._spinner.next_frame()} ")

    def on_mount(self) -> None:
        if self._pending and not self._queued:
            self.start_spinner_timer()

    def set_queued(self, queued: bool) -> None:
        if queued == self._queued:
            return
        self._queued = queued
        if queued:
            self.add_class("queued")
            self.stop_spinning()
            if self._prompt_widget is not None:
                self._prompt_widget.update(self.QUEUED_PROMPT)
            return
        self.remove_class("queued")
        if self._pending:
            if self._prompt_widget is not None:
                self._prompt_widget.update(f"{self._spinner.current_frame()} ")
            self._is_spinning = True
            self.start_spinner_timer()

    def compose(self) -> ComposeResult:
        if self._pending:
            status_class = "bash-pending"
        elif self._exit_code != 0:
            status_class = "bash-error"
        else:
            status_class = "bash-success"
        self.add_class(status_class)
        prompt_text = f"{self._spinner.current_frame()} " if self._pending else "$ "
        with Horizontal(classes="bash-command-line"):
            self._prompt_widget = NonSelectableStatic(
                prompt_text, classes=f"bash-prompt {status_class}"
            )
            yield self._prompt_widget
            yield NoMarkupStatic(self._command, classes="bash-command")
        if not self._pending:
            count = self._overflow_count()
            self._output_widget = NoMarkupStatic(
                self._preview_text(), classes="bash-output"
            )
            self._overflow_widget = NoMarkupStatic(
                self._overflow_text(), classes="bash-output"
            )
            self._section = CollapsibleSection(
                self._overflow_widget, collapsed_label=lines_label(count, prefix="+")
            )
            self._section.display = count > 0
            self._output_container = Horizontal(classes="bash-output-container")
            with self._output_container:
                yield ExpandingBorder(classes="bash-output-border")
                with Vertical(classes="bash-output-body"):
                    yield self._output_widget
                    yield self._section

    async def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        if self._section and self._overflow_count() > 0:
            self._section.toggle()

    async def _ensure_output_container(self) -> None:
        if self._output_container is not None:
            return
        self._output_widget = NoMarkupStatic("", classes="bash-output")
        self._overflow_widget = NoMarkupStatic("", classes="bash-output")
        self._section = CollapsibleSection(
            self._overflow_widget, collapsed_label=lines_label(0, prefix="+")
        )
        self._section.display = False
        self._output_container = Horizontal(
            ExpandingBorder(classes="bash-output-border"),
            Vertical(self._output_widget, self._section, classes="bash-output-body"),
            classes="bash-output-container",
        )
        await self.mount(self._output_container)

    async def append_output(self, text: str) -> None:
        await self._ensure_output_container()
        self._output += text
        self._refresh_output_widgets()

    async def finish(self, exit_code: int, *, interrupted: bool = False) -> None:
        self._exit_code = exit_code
        self._pending = False
        self.stop_spinning()
        if self._prompt_widget:
            self._prompt_widget.update("$ ")
        if interrupted:
            new_class = "bash-interrupted"
        elif exit_code != 0:
            new_class = "bash-error"
        else:
            new_class = "bash-success"
        self.remove_class("bash-pending")
        self.add_class(new_class)
        if self._prompt_widget:
            self._prompt_widget.remove_class("bash-pending")
            self._prompt_widget.add_class(new_class)
        if interrupted:
            suffix = (
                "\n(interrupted)"
                if self._output and not self._output.endswith("\n")
                else "(interrupted)"
            )
            self._output += suffix
        if not self._output:
            self._output = "(no output)"
        await self._ensure_output_container()
        self._refresh_output_widgets()


class ErrorMessage(Static):
    def __init__(
        self, error: str | Content, collapsed: bool = False, show_border: bool = True
    ) -> None:
        super().__init__()
        self.add_class("error-message")
        self._error = error
        self.collapsed = collapsed
        self._show_border = show_border
        self._content_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="error-container"):
            if self._show_border:
                yield ExpandingBorder(classes="error-border")
            error = (
                self._error
                if isinstance(self._error, Content)
                else Content(self._error)
            )
            text = Content("Error: ") + error if self._show_border else error
            self._content_widget = NoMarkupStatic(text, classes="error-content")
            yield self._content_widget

    def set_collapsed(self, collapsed: bool) -> None:
        pass


class HookRunContainer(Vertical):
    def __init__(self) -> None:
        super().__init__(classes="hook-run-container")
        self.display = False

    async def add_message(self, widget: HookSystemMessageLine) -> None:
        await self.mount(widget)
        self.display = True


_HOOK_SEVERITY_ICONS: dict[HookMessageSeverity, str] = {
    HookMessageSeverity.OK: "✓",
    HookMessageSeverity.WARNING: "⚠",
    HookMessageSeverity.ERROR: "✗",
}


class HookSystemMessageLine(Static):
    def __init__(
        self,
        hook_name: str,
        content: str,
        severity: HookMessageSeverity = HookMessageSeverity.WARNING,
    ) -> None:
        super().__init__()
        self.add_class("hook-system-message")
        self.add_class(f"hook-severity-{severity}")
        self._hook_name = hook_name
        self._content = content
        self._severity = severity

    def compose(self) -> ComposeResult:
        icon = _HOOK_SEVERITY_ICONS.get(
            self._severity, _HOOK_SEVERITY_ICONS[HookMessageSeverity.WARNING]
        )
        with Horizontal(classes="hook-system-container"):
            yield NonSelectableStatic(icon, classes="hook-system-icon")
            yield NoMarkupStatic(
                f"[{self._hook_name}] {self._content}", classes="hook-system-content"
            )


class WarningMessage(Static):
    def __init__(self, message: str, show_border: bool = True) -> None:
        super().__init__()
        self.add_class("warning-message")
        self._message = message
        self._show_border = show_border

    def compose(self) -> ComposeResult:
        with Horizontal(classes="warning-container"):
            if self._show_border:
                yield ExpandingBorder(classes="warning-border")
            yield NoMarkupStatic(self._message, classes="warning-content")


class PlanFileMessage(Widget):
    content: reactive[str] = reactive("")

    def __init__(self, file_path: Path) -> None:
        super().__init__()
        self.add_class("plan-file-message")
        self._file_path = file_path
        self._watch_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="plan-file-wrapper"):
            yield Markdown(self.content, classes="plan-file-content")

    def watch_content(self, new_content: str) -> None:
        try:
            self.query_one(Markdown).update(new_content)
        except NoMatches:
            pass

    async def on_mount(self) -> None:
        self.content = (await read_safe_async(self._file_path)).text
        self._watch_task = asyncio.create_task(self._watch_file())

    async def _watch_file(self) -> None:
        try:
            async for _ in awatch(self._file_path):
                self.content = (await read_safe_async(self._file_path)).text
        except (asyncio.CancelledError, FileNotFoundError):
            pass

    def open_in_editor(self) -> None:
        from vibe.cli.textual_ui.external_editor import ExternalEditor

        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.app.suspend():
                ExternalEditor.edit_file(self._file_path)
        except OSError:
            logger.warning(
                "Failed to open plan file in editor: %s", self._file_path, exc_info=True
            )
            self.app.notify(
                f"Could not open plan in editor: {self._file_path}",
                severity="error",
                timeout=6,
            )

    def stop_watching(self) -> None:
        if self._watch_task is None:
            return

        if not self._watch_task.done():
            self._watch_task.cancel()

        self._watch_task = None
