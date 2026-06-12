from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import difflib
from pathlib import Path
import re
from typing import ClassVar

from pydantic import BaseModel
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalGroup
from textual.widget import Widget
from textual.widgets import Markdown, Static

from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection, lines_label
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.tools.builtins.ask_user_question import AskUserQuestionResult
from vibe.core.tools.builtins.bash import BashArgs, BashResult
from vibe.core.tools.builtins.edit import EditArgs, EditResult
from vibe.core.tools.builtins.grep import GrepArgs, GrepResult
from vibe.core.tools.builtins.read import ReadArgs, ReadResult
from vibe.core.tools.builtins.todo import TodoArgs, TodoResult
from vibe.core.tools.builtins.write_file import WriteFileArgs, WriteFileResult

_LINE_NUMBER_PREFIX = re.compile(r"^ *\d+→")
_BACKTICK_RUN = re.compile(r"`+")
_UNSAFE_INFO_STRING = re.compile(r"[^A-Za-z0-9_+\-.]")
_MAX_INFO_STRING_LEN = 32


def _strip_line_numbers(content: str) -> str:
    """Remove the model-facing ``   12→`` line-number prefixes for CLI display."""
    return "\n".join(_LINE_NUMBER_PREFIX.sub("", line) for line in content.split("\n"))


def _fenced_code_block(content: str, ext: str) -> str:
    """Wrap content in a code fence long enough to survive embedded backticks.

    Untrusted content (file/command output) may contain ``` runs that would
    otherwise break out of a fixed three-backtick fence and render as live
    Markdown. CommonMark resolves this by requiring the fence to be strictly
    longer than any backtick run it encloses.

    ``ext`` is derived from attacker-controlled paths in some call sites, so
    strip anything that could escape the fence's info string (newlines,
    backticks, whitespace) and cap the length defensively.
    """
    safe_ext = _UNSAFE_INFO_STRING.sub("", ext)[:_MAX_INFO_STRING_LEN]
    longest_run = max(
        (len(m.group(0)) for m in _BACKTICK_RUN.finditer(content)), default=0
    )
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{safe_ext}\n{content}\n{fence}"


def render_diff_line(line: str) -> Static:
    """Render a single diff line with appropriate styling."""
    if line.startswith("---") or line.startswith("+++"):
        return NoMarkupStatic(line, classes="diff-header")
    elif line.startswith("-"):
        return NoMarkupStatic(line, classes="diff-removed")
    elif line.startswith("+"):
        return NoMarkupStatic(line, classes="diff-added")
    elif line.startswith("@@"):
        return NoMarkupStatic(line, classes="diff-range")
    else:
        return NoMarkupStatic(line, classes="diff-context")


class ToolApprovalWidget[TArgs: BaseModel](Vertical):
    """Base class for approval widgets with typed args."""

    def __init__(self, args: TArgs) -> None:
        super().__init__()
        self.args = args
        self.add_class("tool-approval-widget")

    def compose(self) -> ComposeResult:
        MAX_MSG_SIZE = 150
        model_cls = type(self.args)
        field_names = model_cls.model_fields or self.args.model_extra or {}
        for field_name in field_names:
            value = getattr(self.args, field_name, None)
            if value is None or value in ("", []):
                continue
            value_str = str(value)
            if len(value_str) > MAX_MSG_SIZE:
                hidden = len(value_str) - MAX_MSG_SIZE
                value_str = value_str[:MAX_MSG_SIZE] + f"… ({hidden} more characters)"
            yield NoMarkupStatic(
                f"{field_name}: {value_str}", classes="approval-description"
            )


class ToolResultWidget[TResult: BaseModel](Static):
    PREVIEW_LINES: ClassVar[int] = 0

    def __init__(
        self,
        result: TResult | None,
        success: bool,
        message: str,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.result = result
        self.success = success
        self.message = message
        self.warnings = warnings or []
        self.add_class("tool-result-widget")

    def _footer(self, extra: str | None = None) -> ComposeResult:
        if extra:
            yield NoMarkupStatic(extra, classes="tool-result-hint")

    def _yield_truncated_text(
        self, content: str, *, classes: str = "tool-result-detail"
    ) -> Iterable[Widget]:
        yield from self._yield_truncated(
            content, render=lambda chunk: NoMarkupStatic(chunk, classes=classes)
        )

    def _yield_truncated_markdown(self, content: str, *, ext: str) -> Iterable[Widget]:
        yield from self._yield_truncated(
            content, render=lambda chunk: Markdown(_fenced_code_block(chunk, ext))
        )

    def _yield_truncated(
        self, content: str, *, render: Callable[[str], Widget]
    ) -> Iterable[Widget]:
        if not content:
            return
        lines = content.strip("\n").split("\n")
        if len(lines) <= self.PREVIEW_LINES:
            yield render("\n".join(lines))
            return
        preview = lines[: self.PREVIEW_LINES]
        overflow = lines[self.PREVIEW_LINES :]
        if preview:
            yield render("\n".join(preview))
        yield CollapsibleSection(
            render("\n".join(overflow)),
            collapsed_label=lines_label(len(overflow), prefix="+" if preview else ""),
        )

    def _yield_truncated_widgets(self, widgets: Sequence[Widget]) -> Iterable[Widget]:
        if len(widgets) <= self.PREVIEW_LINES:
            yield from widgets
            return
        preview = widgets[: self.PREVIEW_LINES]
        overflow = widgets[self.PREVIEW_LINES :]
        yield from preview
        overflow_wrapper = (
            VerticalGroup(*overflow) if len(overflow) > 1 else overflow[0]
        )
        yield CollapsibleSection(
            overflow_wrapper,
            collapsed_label=lines_label(len(overflow), prefix="+" if preview else ""),
        )

    def compose(self) -> ComposeResult:
        if self.result:
            lines = [
                f"{field_name}: {value}"
                for field_name in type(self.result).model_fields
                if (value := getattr(self.result, field_name)) is not None
                and value not in ("", [])
            ]
            if lines:
                yield from self._yield_truncated_text("\n".join(lines))
        yield from self._footer()


class BashApprovalWidget(ToolApprovalWidget[BashArgs]):
    def compose(self) -> ComposeResult:
        yield Markdown(_fenced_code_block(self.args.command, "bash"))


class BashResultWidget(ToolResultWidget[BashResult]):
    def _collapsed_output(self) -> str:
        if not self.result:
            return ""
        parts: list[str] = []
        if self.result.stdout:
            parts.append(self.result.stdout.strip("\n"))
        if self.result.stderr:
            parts.append(self.result.stderr.strip("\n"))
        return "\n".join(parts)

    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        output = self._collapsed_output()
        if output:
            yield from self._yield_truncated_text(output)
        else:
            yield NoMarkupStatic("(no content)", classes="tool-result-detail")
        yield from self._footer()


class WriteFileApprovalWidget(ToolApprovalWidget[WriteFileArgs]):
    def compose(self) -> ComposeResult:
        path = Path(self.args.path)
        file_extension = path.suffix.lstrip(".") or "text"

        yield NoMarkupStatic(f"File: {self.args.path}", classes="approval-description")
        yield NoMarkupStatic("")
        yield Markdown(_fenced_code_block(self.args.content, file_extension))


class WriteFileResultWidget(ToolResultWidget[WriteFileResult]):
    PREVIEW_LINES = 20

    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        if self.result.content:
            ext = Path(self.result.path).suffix.lstrip(".") or "text"
            yield from self._yield_truncated_markdown(self.result.content, ext=ext)
        yield from self._footer()


class EditApprovalWidget(ToolApprovalWidget[EditArgs]):
    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(
            f"File: {self.args.file_path}", classes="approval-description"
        )
        yield NoMarkupStatic("")

        old_lines = self.args.old_string.split("\n")
        new_lines = self.args.new_string.split("\n")
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))[2:]
        for line in diff:
            yield render_diff_line(line)

        if self.args.replace_all:
            yield NoMarkupStatic("(replace_all)", classes="approval-description")


class EditResultWidget(ToolResultWidget[EditResult]):
    PREVIEW_LINES = 20

    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        for warning in self.warnings:
            yield NoMarkupStatic(f"⚠ {warning}", classes="tool-result-warning")
        old_lines = self.result.old_string.split("\n")
        new_lines = self.result.new_string.split("\n")
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))[2:]
        diff_widgets = [render_diff_line(line) for line in diff]
        yield from self._yield_truncated_widgets(diff_widgets)
        yield from self._footer()


class TodoApprovalWidget(ToolApprovalWidget[TodoArgs]):
    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(
            f"Action: {self.args.action}", classes="approval-description"
        )
        if self.args.todos:
            yield NoMarkupStatic(
                f"Todos: {len(self.args.todos)} items", classes="approval-description"
            )


class TodoResultWidget(ToolResultWidget[TodoResult]):
    def compose(self) -> ComposeResult:
        if not self.result or not self.result.todos:
            yield NoMarkupStatic("No todos", classes="todo-empty")
            yield from self._footer()
            return

        by_status: dict[str, list] = {
            "in_progress": [],
            "pending": [],
            "completed": [],
            "cancelled": [],
        }
        for todo in self.result.todos:
            status = (
                todo.status.value if hasattr(todo.status, "value") else str(todo.status)
            )
            if status in by_status:
                by_status[status].append(todo)

        for status in ["in_progress", "pending", "completed", "cancelled"]:
            for todo in by_status[status]:
                icon = self._get_status_icon(status)
                yield NoMarkupStatic(f"{icon} {todo.content}", classes=f"todo-{status}")
        yield from self._footer()

    def _get_status_icon(self, status: str) -> str:
        icons = {"pending": "☐", "in_progress": "☐", "completed": "☑", "cancelled": "☒"}
        return icons.get(status, "☐")


class ReadApprovalWidget(ToolApprovalWidget[ReadArgs]):
    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(
            f"file_path: {self.args.file_path}", classes="approval-description"
        )
        if self.args.offset is not None:
            yield NoMarkupStatic(
                f"offset: {self.args.offset}", classes="approval-description"
            )
        if self.args.limit is not None:
            yield NoMarkupStatic(
                f"limit: {self.args.limit}", classes="approval-description"
            )


class ReadResultWidget(ToolResultWidget[ReadResult]):
    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        for warning in self.warnings:
            yield NoMarkupStatic(f"⚠ {warning}", classes="tool-result-warning")
        if self.result.content:
            ext = Path(self.result.file_path).suffix.lstrip(".") or "text"
            yield from self._yield_truncated_markdown(
                _strip_line_numbers(self.result.content), ext=ext
            )
        yield from self._footer()


class GrepApprovalWidget(ToolApprovalWidget[GrepArgs]):
    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(
            f"pattern: {self.args.pattern}", classes="approval-description"
        )
        yield NoMarkupStatic(f"path: {self.args.path}", classes="approval-description")
        if self.args.max_matches is not None:
            yield NoMarkupStatic(
                f"max_matches: {self.args.max_matches}", classes="approval-description"
            )


class GrepResultWidget(ToolResultWidget[GrepResult]):
    def compose(self) -> ComposeResult:
        for warning in self.warnings:
            yield NoMarkupStatic(f"⚠ {warning}", classes="tool-result-warning")
        if not self.result or not self.result.matches:
            yield from self._footer()
            return
        yield from self._yield_truncated_text(self.result.matches)
        yield from self._footer()


class AskUserQuestionResultWidget(ToolResultWidget[AskUserQuestionResult]):
    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return

        answer_widgets: list[Widget] = []
        multi = len(self.result.answers) > 1
        for answer in self.result.answers:
            if multi:
                answer_widgets.append(
                    NoMarkupStatic(answer.question, classes="tool-result-detail")
                )
            prefix = "(Other) " if answer.is_other else ""
            answer_widgets.append(
                NoMarkupStatic(f"{prefix}{answer.answer}", classes="ask-user-answer")
            )
        yield from self._yield_truncated_widgets(answer_widgets)
        yield from self._footer()


APPROVAL_WIDGETS: dict[str, type[ToolApprovalWidget]] = {
    "bash": BashApprovalWidget,
    "read": ReadApprovalWidget,
    "write_file": WriteFileApprovalWidget,
    "edit": EditApprovalWidget,
    "grep": GrepApprovalWidget,
    "todo": TodoApprovalWidget,
}

RESULT_WIDGETS: dict[str, type[ToolResultWidget]] = {
    "bash": BashResultWidget,
    "read": ReadResultWidget,
    "write_file": WriteFileResultWidget,
    "edit": EditResultWidget,
    "grep": GrepResultWidget,
    "todo": TodoResultWidget,
    "ask_user_question": AskUserQuestionResultWidget,
}


def get_approval_widget(tool_name: str, args: BaseModel) -> ToolApprovalWidget:
    widget_class = APPROVAL_WIDGETS.get(tool_name, ToolApprovalWidget)
    return widget_class(args)


def get_result_widget(
    tool_name: str,
    result: BaseModel | None,
    success: bool,
    message: str,
    warnings: list[str] | None = None,
) -> ToolResultWidget:
    widget_class = RESULT_WIDGETS.get(tool_name, ToolResultWidget)
    return widget_class(result, success, message, warnings)
