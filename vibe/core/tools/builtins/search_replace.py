from __future__ import annotations

from collections.abc import AsyncGenerator
import difflib
from pathlib import Path
import re
import shutil
from typing import ClassVar, NamedTuple, final

import anyio
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
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolResultEvent, ToolStreamEvent

SEARCH_REPLACE_BLOCK_RE = re.compile(
    r"<{5,} SEARCH\r?\n(.*?)\r?\n?={5,}\r?\n(.*?)\r?\n?>{5,} REPLACE", flags=re.DOTALL
)

SEARCH_REPLACE_BLOCK_WITH_FENCE_RE = re.compile(
    r"```[\s\S]*?\n<{5,} SEARCH\r?\n(.*?)\r?\n?={5,}\r?\n(.*?)\r?\n?>{5,} REPLACE\s*\n```",
    flags=re.DOTALL,
)


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
    max_content_size: int = 100_000
    create_backup: bool = False
    fuzzy_threshold: float = 0.9
    normalize_whitespace: bool = False
    debug_context_lines: int = 5


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
        blocks = cls._parse_search_replace_blocks(args.content)
        return ToolCallDisplay(
            summary=f"Patching {args.file_path} ({len(blocks)} blocks)",
            content=args.content,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, SearchReplaceResult):
            return ToolResultDisplay(
                success=True,
                message=f"Applied {event.result.blocks_applied} block{'' if event.result.blocks_applied == 1 else 's'}",
                warnings=event.result.warnings,
            )

        return ToolResultDisplay(success=True, message="Patch applied")

    @classmethod
    def get_status_text(cls) -> str:
        return "Editing files"

    def resolve_permission(self, args: SearchReplaceArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.file_path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @final
    async def run(
        self, args: SearchReplaceArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SearchReplaceResult, None]:
        file_path, search_replace_blocks = self._prepare_and_validate_args(args)

        original_content = await self._read_file(file_path)

        block_result = self._apply_blocks(
            original_content,
            search_replace_blocks,
            file_path,
            self.config.fuzzy_threshold,
            self.config.normalize_whitespace,
            self.config.debug_context_lines,
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

            await self._write_file(file_path, modified_content)

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

    async def _read_file(self, file_path: Path) -> str:
        try:
            async with await anyio.Path(file_path).open(encoding="utf-8") as f:
                return await f.read()
        except UnicodeDecodeError as e:
            raise ToolError(f"Unicode decode error reading {file_path}: {e}") from e
        except PermissionError:
            raise ToolError(f"Permission denied reading file: {file_path}")
        except Exception as e:
            raise ToolError(f"Unexpected error reading {file_path}: {e}") from e

    async def _backup_file(self, file_path: Path) -> None:
        shutil.copy2(file_path, file_path.with_suffix(file_path.suffix + ".bak"))

    async def _write_file(self, file_path: Path, content: str) -> None:
        try:
            async with await anyio.Path(file_path).open(
                mode="w", encoding="utf-8"
            ) as f:
                await f.write(content)
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
        normalize_whitespace: bool = False,
        debug_context_lines: int = 5,
    ) -> BlockApplyResult:
        applied = 0
        errors: list[str] = []
        warnings: list[str] = []
        current_content = content

        for i, (search, replace) in enumerate(blocks, 1):
            search_text = search
            
            # Apply whitespace normalization if enabled
            if normalize_whitespace:
                search_text = SearchReplace._normalize_whitespace(search)
                current_content_normalized = SearchReplace._normalize_whitespace(current_content)
                found_in_normalized = search_text in current_content_normalized
            else:
                found_in_normalized = False
            
            found_exact = search in current_content
            
            if not found_exact and not found_in_normalized:
                context = SearchReplace._find_search_context(current_content, search, debug_context_lines)
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
                    "4. Check for typos or case sensitivity issues\n"
                    "5. Consider enabling normalize_whitespace in tool config for more flexible matching"
                )

                errors.append(error_msg)
                continue
            
            # If found in normalized version, use that for replacement
            if found_in_normalized:
                # Find the actual position in original content
                normalized_lines = current_content_normalized.split('\n')
                search_lines = search_text.split('\n')
                original_lines = current_content.split('\n')
                
                # Find matching line range
                for start_idx in range(len(normalized_lines) - len(search_lines) + 1):
                    match = True
                    for j, (norm_line, search_line) in enumerate(zip(normalized_lines[start_idx:start_idx+len(search_lines)], search_lines)):
                        if norm_line.rstrip() != search_line.rstrip():
                            match = False
                            break
                    if match:
                        # Replace in original content
                        replacement_lines = replace.split('\n')
                        new_lines = original_lines[:start_idx] + replacement_lines + original_lines[start_idx+len(search_lines):]
                        current_content = '\n'.join(new_lines)
                        break
            else:
                # Standard replacement
                occurrences = current_content.count(search)
                if occurrences > 1:
                    warning_msg = (
                        f"Search text in block {i} appears {occurrences} times in the file. "
                        f"Only the first occurrence will be replaced. Consider making your "
                        f"search pattern more specific to avoid unintended changes."
                    )
                    warnings.append(warning_msg)

                current_content = current_content.replace(search, replace, 1)
            
            applied += 1

        return BlockApplyResult(
            content=current_content, applied=applied, errors=errors, warnings=warnings
        )

    @final
    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Normalize whitespace for more flexible matching."""
        lines = text.split('\n')
        normalized_lines = []
        
        for line in lines:
            # Preserve indentation but normalize trailing whitespace
            stripped = line.rstrip()
            # Keep leading whitespace (indentation) but remove trailing
            normalized_lines.append(stripped)
        
        return '\n'.join(normalized_lines)

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
        spread = 10  # Increased from 5 to 10 for better coverage

        # Look for anchor lines in content
        for i, line in enumerate(content_lines):
            if first_anchor in line or last_anchor in line:
                start_min = max(0, i - spread)
                start_max = min(len(content_lines) - window_size + 1, i + spread + 1)
                for s in range(start_min, start_max):
                    candidate_starts.add(s)

        if not candidate_starts:
            # If no anchors found, search more broadly
            max_positions = min(len(content_lines) - window_size + 1, 200)  # Increased from 100 to 200
            candidate_starts = set(range(0, max_positions))

        best_match = None
        best_similarity = 0.0

        for start in candidate_starts:
            end = start + window_size
            window_text = "\n".join(content_lines[start:end])

            # Try both exact and normalized matching
            matcher = difflib.SequenceMatcher(None, search_text, window_text)
            similarity = matcher.ratio()

            # Also try with whitespace normalization for better matching
            search_normalized = "\n".join(line.rstrip() for line in search_lines)
            window_normalized = "\n".join(line.rstrip() for line in content_lines[start:end])
            matcher_normalized = difflib.SequenceMatcher(None, search_normalized, window_normalized)
            similarity_normalized = matcher_normalized.ratio()
            similarity = max(similarity, similarity_normalized)

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

        # Look for any of the search lines, not just the first one
        all_search_anchors = [line.strip() for line in search_lines if line.strip()]
        if not all_search_anchors:
            return "Search text contains only whitespace"

        matches = []
        for anchor in all_search_anchors[:3]:  # Check first 3 non-empty lines
            for i, line in enumerate(lines):
                if anchor in line:
                    matches.append((i, anchor))

        if not matches:
            # Provide more detailed analysis of what wasn't found
            missing_info = []
            for i, anchor in enumerate(all_search_anchors[:3]):
                if i == 0:
                    missing_info.append(f"First search line '{anchor}' not found")
                else:
                    missing_info.append(f"Search line {i+1} '{anchor}' not found")
            return "\n".join(missing_info)

        context_lines = []
        for match_idx, anchor in matches[:5]:  # Show up to 5 potential matches
            start = max(0, match_idx - max_context)
            end = min(len(lines), match_idx + max_context + 1)

            context_lines.append(f"\nPotential match area around line {match_idx + 1} (contains '{anchor}'):")
            for i in range(start, end):
                marker = ">>>" if i == match_idx else "   "
                context_lines.append(f"{marker} {i + 1:3d}: {lines[i]}")

        # Add line ending analysis
        line_ending_info = SearchReplace._analyze_line_endings(content, search_text)
        if line_ending_info:
            context_lines.append(f"\n{line_ending_info}")

        return "\n".join(context_lines)

    @final
    @staticmethod
    def _analyze_line_endings(content: str, search_text: str) -> str:
        """Analyze potential line ending mismatches."""
        # Note: When reading files in text mode, Python automatically converts \r\n to \n on Unix
        # So we can only detect line ending issues in the search text itself
        
        search_has_crlf = "\r\n" in search_text
        search_has_lf = "\n" in search_text and not search_has_crlf
        
        messages = []
        
        # Check if search text has mixed line endings
        if search_has_crlf and search_has_lf:
            messages.append("Search text has mixed line endings (both \\r\\n and \\n)")
        elif search_has_crlf:
            messages.append("Search text uses Windows line endings (\\r\\n)")
        elif search_has_lf:
            messages.append("Search text uses Unix line endings (\\n)")
        
        # Check for trailing whitespace issues
        search_lines = search_text.split("\n")
        trailing_whitespace_lines = [i+1 for i, line in enumerate(search_lines) if line != line.rstrip() and line.strip()]
        if trailing_whitespace_lines:
            messages.append(f"Search text has trailing whitespace on lines: {trailing_whitespace_lines}")
        
        return "; ".join(messages) if messages else ""
