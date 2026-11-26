"""Claude project fast discovery and Vibe-project translation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from vibe.core.imports._harness_port import ImportProject


class _ClaudeConfiguration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    projects: dict[str, JsonValue] = Field(default_factory=dict)


class ProjectDirectoryStatus(StrEnum):
    DIRECTORY_EXISTS = auto()
    NO_DIRECTORY = auto()


@dataclass(frozen=True, slots=True)
class ClaudeProject:
    """A project recorded in Claude's local configuration."""

    name: str
    cwd: Path
    directory_status: ProjectDirectoryStatus


class ClaudeProjectSource:
    """Discovers and translates Claude projects."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def discover(self) -> list[ClaudeProject]:
        try:
            configuration = _ClaudeConfiguration.model_validate_json(
                self._config_path.read_text()
            )
        except (OSError, ValidationError):
            return []
        projects: list[ClaudeProject] = []
        for workspace_path in sorted(configuration.projects):
            cwd = Path(workspace_path)
            projects.append(
                ClaudeProject(
                    name=cwd.name or str(cwd),
                    cwd=cwd,
                    directory_status=(
                        ProjectDirectoryStatus.DIRECTORY_EXISTS
                        if cwd.is_dir()
                        else ProjectDirectoryStatus.NO_DIRECTORY
                    ),
                )
            )
        return projects

    def translate(self, source: ClaudeProject) -> ImportProject:
        return ImportProject(name=source.name, cwd=source.cwd)
