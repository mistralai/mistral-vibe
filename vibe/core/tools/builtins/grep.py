from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from enum import StrEnum, auto
from pathlib import Path
import shutil
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, JsonValue

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolStreamEvent
from vibe.core.utils import kill_async_subprocess
from vibe.utils.io import decode_safe, read_safe
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class GrepBackend(StrEnum):
    RIPGREP = auto()
    GNU_GREP = auto()


class GrepToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )

    max_output_bytes: int = Field(
        default=64_000, description="Hard cap for the total size of matched lines."
    )
    default_max_matches: int = Field(
        default=100, description="Default maximum number of matches to return."
    )
    default_timeout: int = Field(
        default=60, description="Default timeout for the search command in seconds."
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


class GrepArgs(BaseModel):
    pattern: str = Field(description="The regex pattern to search for in file contents")
    path: str = Field(
        default=".",
        description="The file or directory to search in. Defaults to the current working directory.",
    )
    max_matches: int | None = Field(
        default=None, description="Override the default maximum number of matches."
    )
    use_default_ignore: bool = Field(
        default=True, description="Whether to respect .gitignore and .ignore files."
    )


class GrepMatch(BaseModel):
    path: str
    line: int | None = None

    @classmethod
    def from_output_line(cls, raw: str, base: Path) -> GrepMatch | None:
        """Parse a single grep/rg output line in `file:line:content` format.

        Handles Windows drive-letter paths like ``C:\\repo\\file.py:10:match``
        by skipping a single-letter first segment. Relative paths (rg/grep emit
        them relative to the search cwd) are anchored on `base`, not the process
        cwd, so the resolved path is correct regardless of where the agent was
        launched from.
        """
        parts = raw.split(":", 3)
        MIN_MATCH_PARTS = 2
        if len(parts) < MIN_MATCH_PARTS:
            return None

        # Windows drive letter: first part is a single letter (e.g. "C")
        MIN_WINDOWS_PARTS = 3
        is_windows_path = (
            len(parts[0]) == 1
            and parts[0].isalpha()
            and len(parts) >= MIN_WINDOWS_PARTS
        )
        if is_windows_path:
            file_path = f"{parts[0]}:{parts[1]}"
            line_str = parts[2]
        else:
            file_path = parts[0]
            line_str = parts[1]

        try:
            line_num = int(line_str) if line_str else None
        except (ValueError, TypeError):
            line_num = None
        return cls(path=str((base / file_path).resolve()), line=line_num)


class GrepResult(BaseModel):
    matches: str
    match_count: int
    pattern: str = ""
    was_truncated: bool = Field(
        description="True if output was cut short by max_matches or max_output_bytes."
    )
    cwd: str = Field(
        default_factory=lambda: str(Path.cwd()),
        exclude=True,
        description=(
            "Search working directory that relative match paths resolve against. "
            "Excluded from serialization: it feeds parsed_matches only, and the "
            "absolute host path must not reach the model-facing result text."
        ),
    )

    @property
    def parsed_matches(self) -> list[GrepMatch]:
        base = Path(self.cwd)
        results: list[GrepMatch] = []
        for line in self.matches.splitlines():
            if match := GrepMatch.from_output_line(line, base):
                results.append(match)
        return results


class Grep(
    BaseTool[GrepArgs, GrepResult, GrepToolConfig, BaseToolState],
    ToolUIData[GrepArgs, GrepResult],
):
    effect_kind = ToolEffectKind.FILE_SEARCH

    @classmethod
    def project_result(cls, result: GrepResult) -> JsonValue:
        return {
            "matches": result.matches,
            "match_count": result.match_count,
            "was_truncated": result.was_truncated,
            "parsed_matches": [
                match.model_dump(mode="json") for match in result.parsed_matches
            ],
        }

    def resolve_permission(self, args: GrepArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
            cwd=self.cwd,
            project_roots=self.harness_files.project_roots,
            scratchpad_dir=self.scratchpad_dir,
        )

    def _detect_backend(self) -> GrepBackend:
        if shutil.which("rg"):
            return GrepBackend.RIPGREP
        if shutil.which("grep"):
            return GrepBackend.GNU_GREP
        raise ToolError(
            "Neither ripgrep (rg) nor grep is installed. "
            "Please install ripgrep: https://github.com/BurntSushi/ripgrep#installation"
        )

    async def run(
        self, args: GrepArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GrepResult, None]:
        backend = self._detect_backend()
        self._validate_args(args)

        exclude_patterns = self._collect_exclude_patterns()
        cmd = self._build_command(args, exclude_patterns, backend)
        stdout = await self._execute_search(cmd)

        yield self._parse_output(
            stdout, args.max_matches or self.config.default_max_matches, args.pattern
        )

    def _validate_args(self, args: GrepArgs) -> None:
        if not args.pattern.strip():
            raise ToolError("Empty search pattern provided.")

        path_obj = Path(args.path).expanduser()
        if not path_obj.is_absolute():
            path_obj = self.cwd / path_obj

        if not path_obj.exists():
            raise ToolError(f"Path does not exist: {args.path}")

    def _collect_exclude_patterns(self) -> list[str]:
        patterns = list(self.config.exclude_patterns)

        codeignore_path = self.cwd / self.config.codeignore_file
        if codeignore_path.is_file():
            patterns.extend(self._load_codeignore_patterns(codeignore_path))

        return patterns

    def _load_codeignore_patterns(self, codeignore_path: Path) -> list[str]:
        patterns = []
        try:
            content = read_safe(codeignore_path).text
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        except OSError:
            pass

        return patterns

    def _build_command(
        self, args: GrepArgs, exclude_patterns: list[str], backend: GrepBackend
    ) -> list[str]:
        if backend == GrepBackend.RIPGREP:
            return self._build_ripgrep_command(args, exclude_patterns)
        return self._build_gnu_grep_command(args, exclude_patterns)

    def _build_ripgrep_command(
        self, args: GrepArgs, exclude_patterns: list[str]
    ) -> list[str]:
        max_matches = args.max_matches or self.config.default_max_matches

        cmd = [
            "rg",
            "--line-number",
            "--no-heading",
            "--with-filename",
            "--smart-case",
            "--no-binary",
            # Request one extra to detect truncation
            "--max-count",
            str(max_matches + 1),
        ]

        if not args.use_default_ignore:
            cmd.append("--no-ignore")

        for pattern in exclude_patterns:
            cmd.extend(["--glob", f"!{pattern}"])

        cmd.extend(["-e", args.pattern, args.path])

        return cmd

    def _build_gnu_grep_command(
        self, args: GrepArgs, exclude_patterns: list[str]
    ) -> list[str]:
        max_matches = args.max_matches or self.config.default_max_matches

        cmd = ["grep", "-r", "-n", "-H", "-I", "-E", f"--max-count={max_matches + 1}"]

        if args.pattern.islower():
            cmd.append("-i")

        for pattern in exclude_patterns:
            if pattern.endswith("/"):
                dir_pattern = pattern.rstrip("/")
                cmd.append(f"--exclude-dir={dir_pattern}")
            else:
                cmd.append(f"--exclude={pattern}")

        cmd.extend(["-e", args.pattern, args.path])

        return cmd

    async def _execute_search(self, cmd: list[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=self.config.default_timeout
                )
            except TimeoutError:
                await kill_async_subprocess(proc, kill_process_group=False)
                raise ToolError(
                    f"Search timed out after {self.config.default_timeout}s"
                )

            stdout = (
                decode_safe(stdout_bytes, from_subprocess=True).text
                if stdout_bytes
                else ""
            )
            stderr = (
                decode_safe(stderr_bytes, from_subprocess=True).text
                if stderr_bytes
                else ""
            )

            if proc.returncode not in {0, 1}:
                error_msg = stderr or f"Process exited with code {proc.returncode}"
                raise ToolError(f"grep error: {error_msg}")

            return stdout

        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Error running grep: {exc}") from exc

    def _parse_output(
        self, stdout: str, max_matches: int, pattern: str = ""
    ) -> GrepResult:
        output_lines = stdout.splitlines() if stdout else []

        truncated_lines = output_lines[:max_matches]
        truncated_output = "\n".join(truncated_lines)

        was_truncated = (
            len(output_lines) > max_matches
            or len(truncated_output) > self.config.max_output_bytes
        )

        final_output = truncated_output[: self.config.max_output_bytes]

        return GrepResult(
            matches=final_output,
            match_count=len(truncated_lines),
            pattern=pattern,
            was_truncated=was_truncated,
            cwd=str(self.cwd),
        )

    @classmethod
    def format_call_display(cls, args: GrepArgs) -> ToolCallDisplay:
        message = f"'{args.pattern}'"
        if args.path != ".":
            message += f" in {args.path}"
        if args.max_matches:
            message += f" (max {args.max_matches} matches)"
        if not args.use_default_ignore:
            message += " [no-ignore]"
        return ToolCallDisplay(
            summary=f"Grepping {message}",
            verb="Searching",
            message=message,
            settled_verb="Searched",
            settled_message=message,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, GrepResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        count = event.result.match_count
        word = "match" if count == 1 else "matches"
        pattern = event.result.pattern
        suffix = "(truncated)" if event.result.was_truncated else ""

        return ToolResultDisplay(
            success=True,
            verb="Searched",
            message=f"{pattern} ({count} {word})" if pattern else f"({count} {word})",
            suffix=suffix,
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Searching files"
