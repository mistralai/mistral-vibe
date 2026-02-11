from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from vibe.cli.textual_ui.ansi_markdown import AnsiMarkdown as Markdown
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.tools.builtins.ask_user_question import AskUserQuestionResult
from vibe.core.tools.builtins.bash import BashArgs, BashResult
from vibe.core.tools.builtins.grep import GrepArgs, GrepResult
from vibe.core.tools.builtins.read_file import ReadFileArgs, ReadFileResult
from vibe.core.tools.builtins.search_replace import (
    SEARCH_REPLACE_BLOCK_RE,
    SearchReplaceArgs,
    SearchReplaceResult,
)
from vibe.core.tools.builtins.todo import TodoArgs, TodoResult
from vibe.core.tools.builtins.write_file import WriteFileArgs, WriteFileResult


def _truncate_lines(content: str, max_lines: int) -> tuple[str, str | None]:
    """Truncate content to max_lines, returning (content, truncation_info)."""
    lines = content.split("\n")
    if len(lines) <= max_lines:
        return content, None
    remaining = len(lines) - max_lines
    return "\n".join(lines[:max_lines]), f"… ({remaining} more lines)"


def parse_search_replace_to_diff(content: str) -> list[str]:
    """Parse SEARCH/REPLACE blocks and generate unified diff lines."""
    all_diff_lines: list[str] = []
    matches = SEARCH_REPLACE_BLOCK_RE.findall(content)
    if not matches:
        return [content[:500]] if content else []

    for i, (search_text, replace_text) in enumerate(matches):
        if i > 0:
            all_diff_lines.append("")  # Separator between blocks
        search_lines = search_text.strip().split("\n")
        replace_lines = replace_text.strip().split("\n")
        diff = difflib.unified_diff(search_lines, replace_lines, lineterm="", n=2)
        all_diff_lines.extend(list(diff)[2:])  # Skip file headers

    return all_diff_lines


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
        for field_name in type(self.args).model_fields:
            value = getattr(self.args, field_name)
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
    """Base class for result widgets with typed result."""

    def __init__(
        self,
        result: TResult | None,
        success: bool,
        message: str,
        collapsed: bool = True,
        warnings: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.result = result
        self.success = success
        self.message = message
        self.collapsed = collapsed
        self.warnings = warnings or []
        self.add_class("tool-result-widget")

    def _footer(self, extra: str | None = None) -> ComposeResult:
        """Yield the footer with optional extra info."""
        if extra:
            yield NoMarkupStatic(extra, classes="tool-result-hint")

    def compose(self) -> ComposeResult:
        """Default: show result fields."""
        if not self.collapsed and self.result:
            for field_name in type(self.result).model_fields:
                value = getattr(self.result, field_name)
                if value is not None and value not in ("", []):
                    yield NoMarkupStatic(
                        f"{field_name}: {value}", classes="tool-result-detail"
                    )
        yield from self._footer()


class BashApprovalWidget(ToolApprovalWidget[BashArgs]):
    def compose(self) -> ComposeResult:
        yield Markdown(f"```bash\n{self.args.command}\n```")


class BashResultWidget(ToolResultWidget[BashResult]):
    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        if self.collapsed:
            truncation_info = None
            if self.result.stdout:
                content, truncation_info = _truncate_lines(self.result.stdout, 10)
                yield NoMarkupStatic(content, classes="tool-result-detail")
            else:
                yield NoMarkupStatic("(no content)", classes="tool-result-detail")
            yield from self._footer(truncation_info)
            return
        yield NoMarkupStatic(
            f"returncode: {self.result.returncode}", classes="tool-result-detail"
        )
        if self.result.stdout:
            sep = "\n" if "\n" in self.result.stdout else " "
            yield NoMarkupStatic(
                f"stdout:{sep}{self.result.stdout}", classes="tool-result-detail"
            )
        if self.result.stderr:
            sep = "\n" if "\n" in self.result.stderr else " "
            yield NoMarkupStatic(
                f"stderr:{sep}{self.result.stderr}", classes="tool-result-detail"
            )
        yield from self._footer()


class WriteFileApprovalWidget(ToolApprovalWidget[WriteFileArgs]):
    def compose(self) -> ComposeResult:
        path = Path(self.args.path)
        file_extension = path.suffix.lstrip(".") or "text"

        yield NoMarkupStatic(f"File: {self.args.path}", classes="approval-description")
        yield NoMarkupStatic("")
        yield Markdown(f"```{file_extension}\n{self.args.content}\n```")


class WriteFileResultWidget(ToolResultWidget[WriteFileResult]):
    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        ext = Path(self.result.path).suffix.lstrip(".") or "text"
        if self.collapsed:
            truncation_info = None
            if self.result.content:
                content, truncation_info = _truncate_lines(self.result.content, 10)
                yield Markdown(f"```{ext}\n{content}\n```")
            yield from self._footer(truncation_info)
            return
        yield NoMarkupStatic(f"Path: {self.result.path}", classes="tool-result-detail")
        yield NoMarkupStatic(
            f"Bytes: {self.result.bytes_written}", classes="tool-result-detail"
        )
        if self.result.content:
            yield NoMarkupStatic("")
            content, _ = _truncate_lines(self.result.content, 10)
            yield Markdown(f"```{ext}\n{content}\n```")
        yield from self._footer()


class SearchReplaceApprovalWidget(ToolApprovalWidget[SearchReplaceArgs]):
    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(
            f"File: {self.args.file_path}", classes="approval-description"
        )
        yield NoMarkupStatic("")

        diff_lines = parse_search_replace_to_diff(self.args.content)
        for line in diff_lines:
            yield render_diff_line(line)


class SearchReplaceResultWidget(ToolResultWidget[SearchReplaceResult]):
    def compose(self) -> ComposeResult:
        if not self.result:
            yield from self._footer()
            return
        for warning in self.warnings:
            yield NoMarkupStatic(f"⚠ {warning}", classes="tool-result-warning")
        if self.result.content:
            for line in parse_search_replace_to_diff(self.result.content):
                yield render_diff_line(line)
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


class ReadFileApprovalWidget(ToolApprovalWidget[ReadFileArgs]):
    def compose(self) -> ComposeResult:
        yield NoMarkupStatic(f"path: {self.args.path}", classes="approval-description")
        if self.args.offset > 0:
            yield NoMarkupStatic(
                f"offset: {self.args.offset}", classes="approval-description"
            )
        if self.args.limit is not None:
            yield NoMarkupStatic(
                f"limit: {self.args.limit}", classes="approval-description"
            )


class ReadFileResultWidget(ToolResultWidget[ReadFileResult]):
    def compose(self) -> ComposeResult:
        if self.collapsed:
            yield from self._footer()
            return
        if self.result:
            yield NoMarkupStatic(
                f"Path: {self.result.path}", classes="tool-result-detail"
            )
        for warning in self.warnings:
            yield NoMarkupStatic(f"⚠ {warning}", classes="tool-result-warning")
        truncation_info = None
        if self.result and self.result.content:
            yield NoMarkupStatic("")
            ext = Path(self.result.path).suffix.lstrip(".") or "text"
            content, truncation_info = _truncate_lines(self.result.content, 10)
            yield Markdown(f"```{ext}\n{content}\n```")
        yield from self._footer(truncation_info)


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
        max_lines = 10 if self.collapsed else None
        if max_lines:
            content, truncation_info = _truncate_lines(self.result.matches, max_lines)
        else:
            content, truncation_info = self.result.matches, None
        yield NoMarkupStatic(content, classes="tool-result-detail")
        yield from self._footer(truncation_info)


class AskUserQuestionResultWidget(ToolResultWidget[AskUserQuestionResult]):
    def compose(self) -> ComposeResult:
        if self.collapsed or not self.result:
            yield from self._footer()
            return

        for answer in self.result.answers:
            if len(self.result.answers) > 1:
                yield NoMarkupStatic(answer.question, classes="tool-result-detail")
            prefix = "(Other) " if answer.is_other else ""
            yield NoMarkupStatic(f"{prefix}{answer.answer}", classes="ask-user-answer")
        yield from self._footer()


APPROVAL_WIDGETS: dict[str, type[ToolApprovalWidget]] = {
    "bash": BashApprovalWidget,
    "read_file": ReadFileApprovalWidget,
    "write_file": WriteFileApprovalWidget,
    "search_replace": SearchReplaceApprovalWidget,
    "grep": GrepApprovalWidget,
    "todo": TodoApprovalWidget,
}

RESULT_WIDGETS: dict[str, type[ToolResultWidget]] = {
    "bash": BashResultWidget,
    "read_file": ReadFileResultWidget,
    "write_file": WriteFileResultWidget,
    "search_replace": SearchReplaceResultWidget,
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
    collapsed: bool = True,
    warnings: list[str] | None = None,
) -> ToolResultWidget:
    widget_class = RESULT_WIDGETS.get(tool_name, ToolResultWidget)
    return widget_class(result, success, message, collapsed, warnings)
