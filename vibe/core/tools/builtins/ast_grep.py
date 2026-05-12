from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class AstGrepBackend(StrEnum):
    PYTHON_API = auto()


class AstGrepToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS

    max_output_bytes: int = Field(
        default=64_000, description="Hard cap for the total size of matched content."
    )
    default_timeout: int = Field(
        default=60, description="Default timeout for the ast-grep operation in seconds."
    )
    exclude_patterns: list[str] = Field(
        default=[
            ".venv/",
            "venv/",
            ".env/",
            "env/",
            "node_modules/",
            ".git/",
            "__pycache__/",
            ".pytest_cache/",
            ".mypy_cache/",
            ".tox/",
            ".nox/",
            ".coverage/",
            "htmlcov/",
            "dist/",
            "build/",
            ".idea/",
            ".vscode/",
            "*.egg-info",
            "*.pyc",
            "*.pyo",
            "*.pyd",
            ".DS_Store",
            "Thumbs.db",
        ],
        description="List of glob patterns to exclude from search (dirs should end with /).",
    )
    codeignore_file: str = Field(
        default=".vibeignore",
        description="Name of the file to read for additional exclusion patterns.",
    )


class AstGrepArgs(BaseModel):
    pattern: str
    path: str = "."
    lang: str | None = Field(
        default=None, description="Language of the pattern (e.g., 'rust', 'python', 'javascript')."
    )
    rewrite: str | None = Field(
        default=None, description="String to replace matched AST nodes."
    )
    selector: str | None = Field(
        default=None, description="AST kind to extract sub-part of pattern to match."
    )
    debug_query: bool = Field(
        default=False, description="Print query pattern's tree-sitter AST for debugging."
    )


class AstGrepResult(BaseModel):
    matches: str
    match_count: int
    was_truncated: bool = Field(
        description="True if output was cut short by max_output_bytes."
    )
    rewritten: bool = Field(
        default=False, description="True if rewrite was applied."
    )


class AstGrep(
    BaseTool[AstGrepArgs, AstGrepResult, AstGrepToolConfig, BaseToolState],
    ToolUIData[AstGrepArgs, AstGrepResult],
):
    description: ClassVar[str] = (
        "Search and rewrite code using AST patterns with ast-grep. "
        "Supports multiple languages and precise AST-based pattern matching."
    )

    def _detect_backend(self) -> AstGrepBackend:
        try:
            import ast_grep_py
            return AstGrepBackend.PYTHON_API
        except ImportError:
            raise ToolError(
                "ast-grep Python package is not installed. "
                "Please install it with: uv add ast-grep-py"
            )

    async def run(
        self, args: AstGrepArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | AstGrepResult, None]:
        self._validate_args(args)

        backend = self._detect_backend()
        result = await self._run_with_python_api(args)
        yield result

    def _validate_args(self, args: AstGrepArgs) -> None:
        if not args.pattern.strip():
            raise ToolError("Empty search pattern provided.")

        path_obj = Path(args.path).expanduser()
        if not path_obj.is_absolute():
            path_obj = Path.cwd() / path_obj

        if not path_obj.exists():
            raise ToolError(f"Path does not exist: {args.path}")

        if args.lang is None:
            raise ToolError("Language must be specified for AST pattern matching.")

    async def _run_with_python_api(self, args: AstGrepArgs) -> AstGrepResult:
        """Run ast-grep using the Python API (ast-grep-py package)."""
        import ast_grep_py as ag

        try:
            # Read the file content
            with open(args.path, 'r', encoding='utf-8') as f:
                source_code = f.read()

            # Create AST root
            root = ag.SgRoot(source_code, args.lang)
            root_node = root.root()

            # Build search parameters
            search_params = {'pattern': args.pattern}

            if args.selector:
                search_params['selector'] = args.selector

            # Find all matches
            matches = root_node.find_all(**search_params)

            # Handle rewrite if requested
            if args.rewrite:
                # Apply rewrite to each match
                rewritten_content = source_code
                edits = []

                for match in matches:
                    # Create a rewrite rule
                    rewrite_rule = ag.Rule(pattern=args.pattern, rewrite=args.rewrite)

                    # Get the match range
                    match_range = match.range()
                    start_pos = match_range.start.index
                    end_pos = match_range.end.index

                    # Apply the rewrite
                    transformed = match.get_transformed(args.rewrite) if hasattr(match, 'get_transformed') else None

                    if transformed:
                        # Add edit
                        edits.append(ag.Edit(
                            start_pos=start_pos,
                            end_pos=end_pos,
                            inserted_text=transformed
                        ))

                # Apply edits if any
                if edits:
                    for edit in reversed(edits):  # Apply from end to start
                        rewritten_content = (
                            rewritten_content[:edit.start_pos] +
                            edit.inserted_text +
                            rewritten_content[edit.end_pos:]
                        )

                result_content = rewritten_content
                is_rewrite = True
            else:
                # Format search results
                result_lines = []
                for i, match in enumerate(matches, 1):
                    match_text = match.text()
                    match_range = match.range()
                    result_lines.append(
                        f"Match {i}: {match_text.strip()}"
                        f" (lines {match_range.start.line + 1}-{match_range.end.line + 1})"
                    )

                result_content = "\n".join(result_lines)
                is_rewrite = False

            # Truncate if needed
            result_content = result_content[: self.config.max_output_bytes]
            was_truncated = len(result_content) >= self.config.max_output_bytes

            return AstGrepResult(
                matches=result_content,
                match_count=len(matches),
                was_truncated=was_truncated,
                rewritten=is_rewrite,
            )

        except Exception as e:
            raise ToolError(f"Error running ast-grep Python API: {e}") from e

    @classmethod
    def format_call_display(cls, args: AstGrepArgs) -> ToolCallDisplay:
        summary = f"AST search '{args.pattern}'"
        if args.path != ".":
            summary += f" in {args.path}"
        if args.lang:
            summary += f" (lang: {args.lang})"
        if args.rewrite:
            summary += f" → '{args.rewrite}'"
        if args.selector:
            summary += f" [selector: {args.selector}]"
        if args.debug_query:
            summary += " [debug]"
        return ToolCallDisplay(summary=summary)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, AstGrepResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.rewritten:
            message = f"Rewrote {event.result.match_count} matches"
        else:
            message = f"Found {event.result.match_count} matches"

        if event.result.was_truncated:
            message += " (truncated)"

        warnings = []
        if event.result.was_truncated:
            warnings.append("Output was truncated due to size limits")

        return ToolResultDisplay(success=True, message=message, warnings=warnings)

    @classmethod
    def get_status_text(cls) -> str:
        return "Analyzing code structure"
