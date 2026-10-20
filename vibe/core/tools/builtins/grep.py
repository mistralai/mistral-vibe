from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.utils import (
    DEFAULT_SENSITIVE_PATTERNS,
    ToolPath,
    resolve_file_tool_permission,
)


class GrepToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
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
    path: ToolPath = Field(
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


class Grep(BaseTool[GrepArgs, GrepResult, GrepToolConfig, BaseToolState]):
    def resolve_permission(self, args: GrepArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
            workspace=self.workspace,
            scratchpad_dir=self.scratchpad_dir,
        )
