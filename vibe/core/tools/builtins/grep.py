from __future__ import annotations

import asyncio
from enum import StrEnum, auto
from pathlib import Path
import shutil
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class GrepBackend(StrEnum):
    RIPGREP = auto()
    GNU_GREP = auto()


class GrepToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS

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


class GrepState(BaseToolState):
    search_history: list[str] = Field(default_factory=list)


class GrepArgs(BaseModel):
    pattern: str
    path: str = "."
    max_matches: int | None = Field(
        default=None, description="Override the default maximum number of matches."
    )
    use_default_ignore: bool = Field(
        default=True, description="Whether to respect .gitignore and .ignore files."
    )


class GrepResult(BaseModel):
    matches: str
    match_count: int
    was_truncated: bool = Field(
        description="True if output was cut short by max_matches or max_output_bytes."
    )


class Grep(
    BaseTool[GrepArgs, GrepResult, GrepToolConfig, GrepState],
    ToolUIData[GrepArgs, GrepResult],
):
    description: ClassVar[str] = (
        "Recursively search files for a regex pattern using ripgrep (rg) or grep. "
        "Respects .gitignore and .codeignore files by default when using ripgrep."
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

    async def run(self, args: GrepArgs) -> GrepResult:
        backend = self._detect_backend()
        self._validate_args(args)
        self.state.search_history.append(args.pattern)

        exclude_patterns = self._collect_exclude_patterns()
        cmd = self._build_command(args, exclude_patterns, backend)
        stdout = await self._execute_search(cmd)

        return self._parse_output(
            stdout, args.max_matches or self.config.default_max_matches
        )

    def _validate_args(self, args: GrepArgs) -> None:
        if not args.pattern.strip():
            raise ToolError("Empty search pattern provided.")

        path_obj = Path(args.path).expanduser()
        if not path_obj.is_absolute():
            path_obj = self.config.effective_workdir / path_obj

        if not path_obj.exists():
            raise ToolError(f"Path does not exist: {args.path}")

    def _collect_exclude_patterns(self) -> list[str]:
        patterns = list(self.config.exclude_patterns)

        codeignore_path = self.config.effective_workdir / self.config.codeignore_file
        if codeignore_path.is_file():
            patterns.extend(self._load_codeignore_patterns(codeignore_path))

        return patterns

    def _load_codeignore_patterns(self, codeignore_path: Path) -> list[str]:
        patterns = []
        try:
            content = codeignore_path.read_text("utf-8")
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

        cmd = ["grep", "-r", "-n", "-I", "-E", f"--max-count={max_matches + 1}"]

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
                cwd=str(self.config.effective_workdir),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=self.config.default_timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise ToolError(
                    f"Search timed out after {self.config.default_timeout}s"
                )

            stdout = (
                stdout_bytes.decode("utf-8", errors="ignore") if stdout_bytes else ""
            )
            stderr = (
                stderr_bytes.decode("utf-8", errors="ignore") if stderr_bytes else ""
            )

            if proc.returncode not in {0, 1}:
                error_msg = stderr or f"Process exited with code {proc.returncode}"
                raise ToolError(f"grep error: {error_msg}")

            return stdout

        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Error running grep: {exc}") from exc

    def _parse_output(self, stdout: str, max_matches: int) -> GrepResult:
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
            was_truncated=was_truncated,
        )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, GrepArgs):
            return ToolCallDisplay(summary="grep")

        summary = f"grep: '{event.args.pattern}'"
        if event.args.path != ".":
            summary += f" in {event.args.path}"
        if event.args.max_matches:
            summary += f" (max {event.args.max_matches} matches)"
        if not event.args.use_default_ignore:
            summary += " [no-ignore]"

        return ToolCallDisplay(
            summary=summary,
            details={
                "pattern": event.args.pattern,
                "path": event.args.path,
                "max_matches": event.args.max_matches,
                "use_default_ignore": event.args.use_default_ignore,
            },
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, GrepResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        message = f"Found {event.result.match_count} matches"
        if event.result.was_truncated:
            message += " (truncated)"

        warnings = []
        if event.result.was_truncated:
            warnings.append("Output was truncated due to size/match limits")

        return ToolResultDisplay(
            success=True,
            message=message,
            warnings=warnings,
            details={
                "match_count": event.result.match_count,
                "was_truncated": event.result.was_truncated,
                "matches": event.result.matches,
            },
        )

    @classmethod
    def get_status_text(cls) -> str:
        """Return status message for spinner."""
        return "Searching files"
