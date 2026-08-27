from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator

from vibe.core.plugins._canonical import NormalizedStr, normalize_nfc


class PluginPathOutsideRootError(ValueError):
    def __init__(self, plugin: str, path: Path) -> None:
        super().__init__(f"path {path} resolves outside plugin {plugin!r}")
        self.plugin = plugin
        self.path = path


# The plugin root spelled as a reference to itself. A hook that runs in the
# plugin root is not the same as a hook with no working directory, so the root
# needs a spelling of its own rather than being dropped.
PLUGIN_ROOT_REF = "."


class PluginPathRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin: NormalizedStr
    path: NormalizedStr

    @field_validator("path")
    @classmethod
    def _reject_non_portable(cls, value: str) -> str:
        if value == PLUGIN_ROOT_REF:
            return value
        candidate = PurePosixPath(value)
        if (
            candidate.is_absolute()
            or not candidate.parts
            or value != candidate.as_posix()
        ):
            raise ValueError("plugin path must be a normalized POSIX relative path")
        if any(part in {".", ".."} for part in candidate.parts):
            raise ValueError("plugin path must not contain '.' or '..' segments")
        return value


def plugin_path_ref(plugin: str, root: Path, target: Path) -> PluginPathRef:
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_root):
        raise PluginPathOutsideRootError(plugin, target)
    relative = resolved_target.relative_to(resolved_root).as_posix()
    return PluginPathRef(plugin=plugin, path=normalize_nfc(relative))


def resolve_plugin_path(ref: PluginPathRef, roots: dict[str, Path]) -> Path:
    return roots[ref.plugin] / PurePosixPath(ref.path)
