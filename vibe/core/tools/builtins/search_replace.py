from __future__ import annotations

from collections.abc import AsyncGenerator
import difflib
from pathlib import Path
import re
import shutil
from typing import ClassVar, NamedTuple, final

import anyio
from pydantic import BaseModel, Field

from vibe.core.rewind.manager import FileSnapshot
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils.io import ReadSafeResult, read_safe_async

SEARCH_REPLACE_BLOCK_RE = re.compile(
    r"<{5,} SEARCH\r?\n(.*?)\r?\n?={5,}\r?\n(.*?)\r?\n?>{5,} REPLACE", flags=re.DOTALL
)

SEARCH_REPLACE_BLOCK_WITH_FENCE_RE = re.compile(
    r"```[\s\S]*?\n<{5,} SEARCH\r?\n(.*?)\r?\n?={5,}\r?\n(.*?)\r?\n?>{5,} REPLACE\s*\n```",
    flags=re.DOTALL,
)
MAX_AMBIGUOUS_MATCH_SUGGESTIONS = 3


class SearchReplaceBlock(NamedTuple):
    search: str
    replace: str


class FuzzyMatch(NamedTuple):
    similarity: float
    start_line: int
    end_line: int
    text: str


class BlockApplyResult(NamedTuple):
    content: str
    applied: int
    errors: list[str]
    warnings: list[str]


class OccurrenceSpan(NamedTuple):
    start: int
    end: int


class LineRange(NamedTuple):
    start: int
    end: int

    def overlaps(self, other: LineRange) -> bool:
        return self.start <= other.end and other.start <= self.end

    def merge(self, other: LineRange) -> LineRange:
        return LineRange(
            start=min(self.start, other.start), end=max(self.end, other.end)
        )


class SearchReplaceArgs(BaseModel):
    file_path: str
    content: str


class SearchReplaceResult(BaseModel):
    file: str
    blocks_applied: int
    lines_changed: int
    content: str
    warnings: list[str] = Field(default_factory=list)


class SearchReplaceConfig(BaseToolConfig):
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    max_content_size: int = 100_000
    create_backup: bool = False
    fuzzy_threshold: float = 0.9


class SearchReplace(
    BaseTool[
        SearchReplaceArgs, SearchReplaceResult, SearchReplaceConfig, BaseToolState
    ],
    ToolUIData[SearchReplaceArgs, SearchReplaceResult],
):
    description: ClassVar[str] = (
        "Replace sections of files using SEARCH/REPLACE blocks. "
        "Supports fuzzy matching and detailed error reporting. "
        "Format: <<<<<<< SEARCH\\n[text]\\n=======\\n[replacement]\\n>>>>>>> REPLACE"
    )

    @classmethod
    def format_call_display(cls, args: SearchReplaceArgs) -> ToolCallDisplay:
        tag = " (scratchpad)" if is_scratchpad_path(args.file_path) else ""
        blocks = cls._parse_search_replace_blocks(args.content)
        return ToolCallDisplay(
            summary=f"Patching {args.file_path} ({len(blocks)} blocks){tag}",
            content=args.content,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, SearchReplaceResult):
            path_name = Path(event.result.file).name
            tag = " (scratchpad)" if is_scratchpad_path(event.result.file) else ""
            return ToolResultDisplay(
                success=True,
                message=f"Applied {event.result.blocks_applied} block{'' if event.result.blocks_applied == 1 else 's'} to {path_name}{tag}",
                warnings=event.result.warnings,
            )

        return ToolResultDisplay(success=True, message="Patch applied")

    @classmethod
    def get_status_text(cls) -> str:
        return "Editing files"

    def get_file_snapshot(self, args: SearchReplaceArgs) -> FileSnapshot | None:
        return self.get_file_snapshot_for_path(args.file_path)

    def resolve_permission(self, args: SearchReplaceArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.file_path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    @final
    async def run(
        self, args: SearchReplaceArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SearchReplaceResult, None]:
        file_path, search_replace_blocks = self._prepare_and_validate_args(args)

        decoded = await self._read_file(file_path)
        original_content = decoded.text

        block_result = self._apply_blocks(
            original_content,
            search_replace_blocks,
            file_path,
            self.config.fuzzy_threshold,
        )

        if block_result.errors:
            error_message = "SEARCH/REPLACE blocks failed:\n" + "\n\n".join(
                block_result.errors
            )
            if block_result.warnings:
                error_message += "\n\nWarnings encountered:\n" + "\n".join(
                    block_result.warnings
                )
            raise ToolError(error_message)

        modified_content = block_result.content

        # Calculate line changes
        if modified_content == original_content:
            lines_changed = 0
        else:
            original_lines = len(original_content.splitlines())
            new_lines = len(modified_content.splitlines())
            lines_changed = new_lines - original_lines

            try:
                if self.config.create_backup:
                    await self._backup_file(file_path)
            except Exception:
                pass

            await self._write_file(file_path, modified_content, decoded.encoding)

        yield SearchReplaceResult(
            file=str(file_path),
            blocks_applied=block_result.applied,
            lines_changed=lines_changed,
            warnings=block_result.warnings,
            content=args.content,
        )

    @final
    def _prepare_and_validate_args(
        self, args: SearchReplaceArgs
    ) -> tuple[Path, list[SearchReplaceBlock]]:
        file_path_str = args.file_path.strip()
        content = args.content.strip()

        if not file_path_str:
            raise ToolError("File path cannot be empty")

        if len(content) > self.config.max_content_size:
            raise ToolError(
                f"Content size ({len(content)} bytes) exceeds max_content_size "
                f"({self.config.max_content_size} bytes)"
            )

        if not content:
            raise ToolError("Empty content provided")

        project_root = Path.cwd()
        file_path = Path(file_path_str).expanduser()
        if not file_path.is_absolute():
            file_path = project_root / file_path
        file_path = file_path.resolve()

        if not file_path.exists():
            raise ToolError(f"File does not exist: {file_path}")

        if not file_path.is_file():
            raise ToolError(f"Path is not a file: {file_path}")

        search_replace_blocks = self._parse_search_replace_blocks(content)
        if not search_replace_blocks:
            raise ToolError(
                "No valid SEARCH/REPLACE blocks found in content.\n"
                "Expected format:\n"
                "<<<<<<< SEARCH\n"
                "[exact content to find]\n"
                "=======\n"
                "[new content to replace with]\n"
                ">>>>>>> REPLACE"
            )

        return file_path, search_replace_blocks

    async def _read_file(self, file_path: Path) -> ReadSafeResult:
        try:
            return await read_safe_async(file_path, raise_on_error=True)
        except PermissionError:
            raise ToolError(f"Permission denied reading file: {file_path}")
        except OSError as e:
            raise ToolError(f"OS error reading {file_path}: {e}") from e
        except Exception as e:
            raise ToolError(f"Unexpected error reading {file_path}: {e}") from e

    async def _backup_file(self, file_path: Path) -> None:
        shutil.copy2(file_path, file_path.with_suffix(file_path.suffix + ".bak"))

    async def _write_file(self, file_path: Path, content: str, encoding: str) -> None:
        try:
            async with await anyio.Path(file_path).open(
                mode="w", encoding=encoding
            ) as f:
                await f.write(content)
        except UnicodeEncodeError as e:
            raise ToolError(
                f"Cannot encode patched content for {file_path} using {encoding!r}: {e}"
            ) from e
        except PermissionError:
            raise ToolError(f"Permission denied writing to file: {file_path}")
        except OSError as e:
            raise ToolError(f"OS error writing to {file_path}: {e}") from e
        except Exception as e:
            raise ToolError(f"Unexpected error writing to {file_path}: {e}") from e

    @final
    @staticmethod
    def _apply_blocks(
        content: str,
        blocks: list[SearchReplaceBlock],
        filepath: Path,
        fuzzy_threshold: float = 0.9,
    ) -> BlockApplyResult:
        applied = 0
        errors: list[str] = []
        warnings: list[str] = []
        current_content = content

        for i, (search, replace) in enumerate(blocks, 1):
            if not search:
                errors.append(
                    f"SEARCH/REPLACE block {i} failed: Search text cannot be empty."
                )
                continue

            if search not in current_content:
                context = SearchReplace._find_search_context(current_content, search)
                fuzzy_context = SearchReplace._find_fuzzy_match_context(
                    current_content, search, fuzzy_threshold
                )

                error_msg = (
                    f"SEARCH/REPLACE block {i} failed: Search text not found in {filepath}\n"
                    f"Search text was:\n{search!r}\n"
                    f"Context analysis:\n{context}"
                )

                if fuzzy_context:
                    error_msg += f"\n{fuzzy_context}"

                error_msg += (
                    "\nDebugging tips:\n"
                    "1. Check for exact whitespace/indentation match\n"
                    "2. Verify line endings match the file exactly (\\r\\n vs \\n)\n"
                    "3. Ensure the search text hasn't been modified by previous blocks or user edits\n"
                    "4. Check for typos or case sensitivity issues"
                )

                errors.append(error_msg)
                continue

            occurrences = SearchReplace._find_occurrences(current_content, search)
            if len(occurrences) > 1:
                errors.append(
                    SearchReplace._format_ambiguous_search_error(
                        block_index=i,
                        filepath=filepath,
                        content=current_content,
                        search=search,
                        replace=replace,
                        occurrences=occurrences,
                    )
                )
                continue

            current_content = current_content.replace(search, replace, 1)
            applied += 1

        return BlockApplyResult(
            content=current_content, applied=applied, errors=errors, warnings=warnings
        )

    @final
    @staticmethod
    def _find_occurrences(content: str, search: str) -> list[OccurrenceSpan]:
        occurrences: list[OccurrenceSpan] = []
        start = 0

        while True:
            match_start = content.find(search, start)
            if match_start == -1:
                return occurrences

            match_end = match_start + len(search)
            occurrences.append(OccurrenceSpan(match_start, match_end))
            start = match_end

    @final
    @staticmethod
    def _format_ambiguous_search_error(
        block_index: int,
        filepath: Path,
        content: str,
        search: str,
        replace: str,
        occurrences: list[OccurrenceSpan],
    ) -> str:
        count = len(occurrences)
        message = (
            f"SEARCH/REPLACE block {block_index} failed: Search text is ambiguous in "
            f"{filepath}. It appears {count} times. The tool will not guess which "
            "occurrence to replace."
        )

        if count > MAX_AMBIGUOUS_MATCH_SUGGESTIONS:
            return message

        regions = SearchReplace._build_disambiguated_regions(content, occurrences)
        if not regions:
            return (
                f"{message}\nCould not build unique expanded source regions "
                "from the matching regions. Add more surrounding context manually."
            )

        return (
            f"{message}\nExpanded source region"
            f"{'' if len(regions) == 1 else 's'}:\n\n" + "\n\n".join(regions)
        )

    @final
    @staticmethod
    def _build_disambiguated_regions(
        content: str, occurrences: list[OccurrenceSpan]
    ) -> list[str]:
        lines = content.splitlines(keepends=True)
        if not lines:
            return []

        line_starts = SearchReplace._line_starts(lines)
        ranges: list[LineRange] = []

        for occurrence in occurrences:
            line_range = SearchReplace._line_range_for_span(occurrence, line_starts)
            unique = SearchReplace._find_unique_expanded_region(
                content, lines, line_range
            )
            if unique is None:
                continue

            ranges.append(unique)

        return [
            SearchReplace._format_source_region(lines, line_range)
            for line_range in SearchReplace._merge_line_ranges(ranges)
        ]

    @final
    @staticmethod
    def _merge_line_ranges(ranges: list[LineRange]) -> list[LineRange]:
        if not ranges:
            return []

        merged: list[LineRange] = []
        for line_range in sorted(ranges, key=lambda r: r.start):
            if merged and merged[-1].overlaps(line_range):
                merged[-1] = merged[-1].merge(line_range)
                continue
            merged.append(line_range)

        return merged

    @final
    @staticmethod
    def _format_source_region(lines: list[str], line_range: LineRange) -> str:
        source = "".join(lines[line_range.start : line_range.end + 1]).rstrip("\r\n")
        return (
            f"Source region lines {line_range.start + 1}-{line_range.end + 1}:\n"
            f"```\n{source}\n```"
        )

    @final
    @staticmethod
    def _line_starts(lines: list[str]) -> list[int]:
        starts: list[int] = []
        offset = 0
        for line in lines:
            starts.append(offset)
            offset += len(line)
        return starts

    @final
    @staticmethod
    def _line_range_for_span(
        occurrence: OccurrenceSpan, line_starts: list[int]
    ) -> LineRange:
        start_line = 0
        end_line = len(line_starts) - 1

        for i, line_start in enumerate(line_starts):
            if line_start <= occurrence.start:
                start_line = i
            if line_start < occurrence.end:
                end_line = i
                continue
            break

        return LineRange(start_line, end_line)

    @final
    @staticmethod
    def _find_unique_expanded_region(
        content: str, lines: list[str], line_range: LineRange
    ) -> LineRange | None:
        max_radius = max(line_range.start, len(lines) - line_range.end - 1)

        for radius in range(1, max_radius + 1):
            start = max(0, line_range.start - radius)
            end = min(len(lines) - 1, line_range.end + radius)
            candidate = "".join(lines[start : end + 1])
            if content.count(candidate) == 1:
                return LineRange(start, end)

        candidate = "".join(lines[line_range.start : line_range.end + 1])
        if content.count(candidate) == 1:
            return line_range

        return None

    @final
    @staticmethod
    def _find_fuzzy_match_context(
        content: str, search_text: str, threshold: float = 0.9
    ) -> str | None:
        best_match = SearchReplace._find_best_fuzzy_match(
            content, search_text, threshold
        )

        if not best_match:
            return None

        diff = SearchReplace._create_unified_diff(
            search_text, best_match.text, "SEARCH", "CLOSEST MATCH"
        )

        similarity_pct = best_match.similarity * 100

        return (
            f"Closest fuzzy match (similarity {similarity_pct:.1f}%) "
            f"at lines {best_match.start_line}–{best_match.end_line}:\n"
            f"```diff\n{diff}\n```"
        )

    @final
    @staticmethod
    def _find_best_fuzzy_match(  # noqa: PLR0914
        content: str, search_text: str, threshold: float = 0.9
    ) -> FuzzyMatch | None:
        content_lines = content.split("\n")
        search_lines = search_text.split("\n")
        window_size = len(search_lines)

        if window_size == 0:
            return None

        non_empty_search = [line for line in search_lines if line.strip()]
        if not non_empty_search:
            return None

        first_anchor = non_empty_search[0]
        last_anchor = (
            non_empty_search[-1] if len(non_empty_search) > 1 else first_anchor
        )

        candidate_starts = set()
        spread = 5

        for i, line in enumerate(content_lines):
            if first_anchor in line or last_anchor in line:
                start_min = max(0, i - spread)
                start_max = min(len(content_lines) - window_size + 1, i + spread + 1)
                for s in range(start_min, start_max):
                    candidate_starts.add(s)

        if not candidate_starts:
            max_positions = min(len(content_lines) - window_size + 1, 100)
            candidate_starts = set(range(0, max_positions))

        best_match = None
        best_similarity = 0.0

        for start in candidate_starts:
            end = start + window_size
            window_text = "\n".join(content_lines[start:end])

            matcher = difflib.SequenceMatcher(None, search_text, window_text)
            similarity = matcher.ratio()

            if similarity >= threshold and similarity > best_similarity:
                best_similarity = similarity
                best_match = FuzzyMatch(
                    similarity=similarity,
                    start_line=start + 1,  # 1-based line numbers
                    end_line=end,
                    text=window_text,
                )

        return best_match

    @final
    @staticmethod
    def _create_unified_diff(
        text1: str, text2: str, label1: str = "SEARCH", label2: str = "CLOSEST MATCH"
    ) -> str:
        lines1 = text1.splitlines(keepends=True)
        lines2 = text2.splitlines(keepends=True)

        lines1 = [line if line.endswith("\n") else line + "\n" for line in lines1]
        lines2 = [line if line.endswith("\n") else line + "\n" for line in lines2]

        diff = difflib.unified_diff(
            lines1, lines2, fromfile=label1, tofile=label2, lineterm="", n=3
        )

        diff_lines = list(diff)

        if diff_lines and not diff_lines[0].startswith("==="):
            diff_lines.insert(2, "=" * 67 + "\n")

        result = "".join(diff_lines)

        max_chars = 2000
        if len(result) > max_chars:
            result = result[:max_chars] + "\n...(diff truncated)"

        return result.rstrip()

    @final
    @staticmethod
    def _parse_search_replace_blocks(content: str) -> list[SearchReplaceBlock]:
        """Parse SEARCH/REPLACE blocks from content.

        Supports two formats:
        1. With code block fences (```...```)
        2. Without code block fences
        """
        matches = SEARCH_REPLACE_BLOCK_WITH_FENCE_RE.findall(content)

        if not matches:
            matches = SEARCH_REPLACE_BLOCK_RE.findall(content)

        return [
            SearchReplaceBlock(
                search=search.rstrip("\r\n"), replace=replace.rstrip("\r\n")
            )
            for search, replace in matches
        ]

    @final
    @staticmethod
    def _find_search_context(
        content: str, search_text: str, max_context: int = 5
    ) -> str:
        lines = content.split("\n")
        search_lines = search_text.split("\n")

        if not search_lines:
            return "Search text is empty"

        first_search_line = search_lines[0].strip()
        if not first_search_line:
            return "First line of search text is empty or whitespace only"

        matches = []
        for i, line in enumerate(lines):
            if first_search_line in line:
                matches.append(i)

        if not matches:
            return f"First search line '{first_search_line}' not found anywhere in file"

        context_lines = []
        for match_idx in matches[:3]:
            start = max(0, match_idx - max_context)
            end = min(len(lines), match_idx + max_context + 1)

            context_lines.append(f"\nPotential match area around line {match_idx + 1}:")
            for i in range(start, end):
                marker = ">>>" if i == match_idx else "   "
                context_lines.append(f"{marker} {i + 1:3d}: {lines[i]}")

        return "\n".join(context_lines)
