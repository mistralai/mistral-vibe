from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibe.core.tools.ui import ToolResultDisplay

from vibe.cli.textual_ui.widgets.tool_widgets import (
    BashApprovalWidget,
    BashResultWidget,
    GrepApprovalWidget,
    GrepResultWidget,
    ReadFileApprovalWidget,
    ReadFileResultWidget,
    SearchReplaceApprovalWidget,
    SearchReplaceResultWidget,
    TodoApprovalWidget,
    TodoResultWidget,
    ToolApprovalWidget,
    ToolResultWidget,
    WriteFileApprovalWidget,
    WriteFileResultWidget,
)


class ToolRenderer:
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[ToolApprovalWidget], dict[str, Any]]:
        return ToolApprovalWidget, tool_args

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[ToolResultWidget], dict[str, Any]]:
        data = {
            "success": display.success,
            "message": display.message,
            "details": self._clean_details(display.details),
            "warnings": display.warnings,
        }
        return ToolResultWidget, data

    def _clean_details(self, details: dict) -> dict:
        clean = {}
        for key, value in details.items():
            if value is None or value in ("", []):
                continue
            value_str = str(value).strip().replace("\n", " ").replace("\r", "")
            value_str = " ".join(value_str.split())
            if value_str:
                clean[key] = value_str
        return clean


class BashRenderer(ToolRenderer):
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[BashApprovalWidget], dict[str, Any]]:
        data = {
            "command": tool_args.get("command", ""),
            "description": tool_args.get("description", ""),
        }
        return BashApprovalWidget, data

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[BashResultWidget], dict[str, Any]]:
        data = {
            "success": display.success,
            "message": display.message,
            "details": self._clean_details(display.details),
            "warnings": display.warnings,
        }
        return BashResultWidget, data


class WriteFileRenderer(ToolRenderer):
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[WriteFileApprovalWidget], dict[str, Any]]:
        data = {
            "path": tool_args.get("path", ""),
            "content": tool_args.get("content", ""),
            "file_extension": tool_args.get("file_extension", "text"),
        }
        return WriteFileApprovalWidget, data

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[WriteFileResultWidget], dict[str, Any]]:
        data = {
            "success": display.success,
            "message": display.message,
            "path": display.details.get("path", ""),
            "bytes_written": display.details.get("bytes_written"),
            "content": display.details.get("content", ""),
            "file_extension": display.details.get("file_extension", "text"),
        }
        return WriteFileResultWidget, data


class SearchReplaceRenderer(ToolRenderer):
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[SearchReplaceApprovalWidget], dict[str, Any]]:
        file_path = tool_args.get("file_path", "")
        content = str(tool_args.get("content", ""))

        diff_lines = self._parse_search_replace_blocks(content)

        data = {"file_path": file_path, "diff_lines": diff_lines}
        return SearchReplaceApprovalWidget, data

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[SearchReplaceResultWidget], dict[str, Any]]:
        diff_lines = self._parse_search_replace_blocks(
            display.details.get("content", "")
        )
        data = {
            "success": display.success,
            "message": display.message,
            "diff_lines": diff_lines if not collapsed else [],
        }
        return SearchReplaceResultWidget, data

    def _parse_search_replace_blocks(self, content: str) -> list[str]:
        if "<<<<<<< SEARCH" not in content:
            return [content]

        try:
            sections = content.split("<<<<<<< SEARCH")
            rest = sections[1].split("=======")
            search_section = rest[0].strip()
            replace_part = rest[1].split(">>>>>>> REPLACE")
            replace_section = replace_part[0].strip()

            search_lines = search_section.split("\n")
            replace_lines = replace_section.split("\n")

            diff = difflib.unified_diff(search_lines, replace_lines, lineterm="", n=2)
            return list(diff)[2:]  # Skip file headers
        except (IndexError, AttributeError):
            return [content[:500]]


class TodoRenderer(ToolRenderer):
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[TodoApprovalWidget], dict[str, Any]]:
        data = {"description": tool_args.get("description", "")}
        return TodoApprovalWidget, data

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[TodoResultWidget], dict[str, Any]]:
        data = {
            "success": display.success,
            "message": display.message,
            "todos_by_status": display.details.get("todos_by_status", {}),
        }
        return TodoResultWidget, data


class ReadFileRenderer(ToolRenderer):
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[ReadFileApprovalWidget], dict[str, Any]]:
        return ReadFileApprovalWidget, tool_args

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[ReadFileResultWidget], dict[str, Any]]:
        data = {
            "success": display.success,
            "message": display.message,
            "path": display.details.get("path", ""),
            "warnings": display.warnings,
            "content": display.details.get("content", "") if not collapsed else "",
            "file_extension": display.details.get("file_extension", "text"),
        }
        return ReadFileResultWidget, data


class GrepRenderer(ToolRenderer):
    def get_approval_widget(
        self, tool_args: dict
    ) -> tuple[type[GrepApprovalWidget], dict[str, Any]]:
        return GrepApprovalWidget, tool_args

    def get_result_widget(
        self, display: ToolResultDisplay, collapsed: bool
    ) -> tuple[type[GrepResultWidget], dict[str, Any]]:
        data = {
            "success": display.success,
            "message": display.message,
            "warnings": display.warnings,
            "matches": display.details.get("matches", "") if not collapsed else "",
        }
        return GrepResultWidget, data


_RENDERER_REGISTRY: dict[str, type[ToolRenderer]] = {
    "write_file": WriteFileRenderer,
    "search_replace": SearchReplaceRenderer,
    "todo": TodoRenderer,
    "read_file": ReadFileRenderer,
    "bash": BashRenderer,
    "grep": GrepRenderer,
}


def get_renderer(tool_name: str) -> ToolRenderer:
    renderer_class = _RENDERER_REGISTRY.get(tool_name, ToolRenderer)
    return renderer_class()
