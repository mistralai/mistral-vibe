"""Skill folders read and written as files, for the Skills page editor.

A skill is addressed by its folder name; sessions know it by the ``name`` in
its frontmatter, which is what ``disabled_skills`` lists. A read returns a
``version`` hashed over its files; a write naming a stale version is refused.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import threading
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import TypeAdapter, ValidationError
import yaml

from vibe.core.config.harness_files._paths import (
    GLOBAL_AGENTS_SKILLS_DIR,
    GLOBAL_SKILLS_DIR,
)
from vibe.core.skills.models import SkillMetadata
from vibe.core.skills.parser import SkillParseError, parse_skill_markdown
from vibe.utils.io import read_safe
from vibe.utils.paths import resolves_within

if TYPE_CHECKING:
    from vibe.app_server.protocol import LocalSkillRefusalReason

ENTRY_FILE = "SKILL.md"

# The editor's own limits.
MAX_ASSET_COUNT = 200
MAX_ASSET_BYTES = 256 * 1024
MAX_SKIPPED_PATHS = 100

# What a save interrupted by a hard kill leaves: `.{name}.{16 hex}`.
_LEFTOVER = re.compile(r"\..+\.[0-9a-f]{16}")

_DESCRIPTION = TypeAdapter(Annotated[str, SkillMetadata.model_fields["description"]])


class LocalSkillError(Exception):
    """``reason`` is set only for refusals the editor cannot check before saving."""

    def __init__(
        self, message: str, reason: LocalSkillRefusalReason | None = None
    ) -> None:
        super().__init__(message)
        self.reason: LocalSkillRefusalReason | None = reason


class LocalSkillNotFound(Exception):
    pass


class LocalSkillConflict(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LocalSkillFiles:
    folder: str
    name: str
    description: str
    body: str
    assets: dict[str, str]
    # Binary, oversized or past the count limit; a write leaves them alone.
    skipped_paths: list[str]
    path: Path
    version: str


@dataclass(frozen=True, slots=True)
class LocalSkillEntry:
    folder: str
    name: str
    description: str
    path: Path


def skill_roots(project_root: Path | None) -> list[Path]:
    if project_root is None:
        return [GLOBAL_SKILLS_DIR.path, GLOBAL_AGENTS_SKILLS_DIR.path]
    return [project_root / ".vibe" / "skills", project_root / ".agents" / "skills"]


def _new_skill_root(project_root: Path | None) -> Path:
    # A project shares its skills with every agent harness, so new ones go
    # where all of them read: `.agents/skills`.
    roots = skill_roots(project_root)
    return roots[0] if project_root is None else roots[1]


def list_local_skills(project_root: Path | None) -> list[LocalSkillEntry]:
    """By folder name; a folder in both roots is the first one's, as sessions see it.

    A project folder whose links lead outside the project is left out.
    """
    entries: dict[str, LocalSkillEntry] = {}
    with _os_errors():
        for root in skill_roots(project_root):
            if not root.is_dir() or not _within_project(root, project_root):
                continue
            for skill_dir in sorted(root.iterdir()):
                entry = skill_dir / ENTRY_FILE
                if _LEFTOVER.fullmatch(skill_dir.name):
                    continue
                if skill_dir.name in entries or not entry.is_file():
                    continue
                if not _within_project(entry, project_root):
                    continue
                try:
                    frontmatter, _ = parse_skill_markdown(read_safe(entry).text)
                except (OSError, SkillParseError):
                    continue
                entries[skill_dir.name] = LocalSkillEntry(
                    folder=skill_dir.name,
                    name=_skill_name(frontmatter, skill_dir.name),
                    description=_description(frontmatter),
                    path=skill_dir,
                )
    return sorted(entries.values(), key=lambda skill: skill.folder)


# Writes run in worker threads; the version check and the write must not interleave.
_WRITE_LOCK = threading.Lock()


def read_local_skill(folder: str, project_root: Path | None) -> LocalSkillFiles:
    with _os_errors():
        skill_dir = _existing_skill_dir(folder, project_root)
        entry = skill_dir / ENTRY_FILE
        try:
            frontmatter, body = parse_skill_markdown(read_safe(entry).text)
        except SkillParseError as exc:
            raise LocalSkillError(f"Cannot read {entry}: {exc}", "unreadable") from exc
        assets, skipped = _read_assets(skill_dir)
        return LocalSkillFiles(
            folder=folder,
            name=_skill_name(frontmatter, folder),
            description=_description(frontmatter),
            body=body.lstrip("\r\n").rstrip(),
            assets=assets,
            skipped_paths=skipped,
            path=skill_dir,
            version=_version(entry, assets, skipped),
        )


def write_local_skill(
    *,
    folder: str,
    description: str,
    body: str,
    assets: Mapping[str, str],
    expected_version: str | None,
    project_root: Path | None,
) -> LocalSkillFiles:
    """Creates the skill without ``expected_version``, else updates it.

    A new skill is named after its folder. An update keeps the frontmatter
    ``name`` and every key the editor does not show; asset files no longer
    named are removed.
    """
    try:
        if expected_version is None:
            SkillMetadata.model_validate({"name": folder, "description": description})
        else:
            _DESCRIPTION.validate_python(description)
    except ValidationError as exc:
        raise LocalSkillError(str(exc)) from exc
    clean_assets = {_asset_path(path): content for path, content in assets.items()}
    _check_case_collisions(clean_assets)
    if len(clean_assets) > MAX_ASSET_COUNT:
        raise LocalSkillError(f"A skill holds at most {MAX_ASSET_COUNT} files")
    for path, content in clean_assets.items():
        if len(content.encode()) > MAX_ASSET_BYTES:
            raise LocalSkillError(f"{path} is larger than {MAX_ASSET_BYTES} bytes")

    with _WRITE_LOCK, _os_errors():
        if expected_version is None:
            if _has_skill(folder, project_root):
                raise LocalSkillConflict(f"A skill named {folder!r} already exists")
            skill_dir = _in_project(
                _new_skill_root(project_root) / folder, project_root
            )
            # Not a skill sessions load, and not ours to replace.
            if os.path.lexists(skill_dir):
                raise LocalSkillError(
                    f"{skill_dir} is in the way; move it first", "in_the_way"
                )
            frontmatter: dict[str, Any] = {"name": folder}
            previous_assets: dict[str, str] = {}
        else:
            current = read_local_skill(folder, project_root)
            skill_dir = current.path
            if current.version != expected_version:
                raise LocalSkillConflict(
                    f"{folder!r} changed on disk since it was opened; reload it first"
                )
            frontmatter, _ = parse_skill_markdown(
                read_safe(skill_dir / ENTRY_FILE).text
            )
            frontmatter["name"] = current.name
            previous_assets = current.assets
            skipped = {path.casefold() for path in current.skipped_paths}
            kept = {path for path in clean_assets if path.casefold() in skipped}
            if kept:
                raise LocalSkillError(
                    f"{sorted(kept)[0]} is a file the editor cannot show; rename yours",
                    "skipped_file",
                )
        removed = previous_assets.keys() - clean_assets.keys()
        for path in [ENTRY_FILE, *clean_assets, *removed]:
            _contained(skill_dir / path, skill_dir)

        entry = _render_entry({**frontmatter, "description": description}, body)
        if expected_version is None:
            _create_skill_dir(skill_dir, entry, clean_assets)
        else:
            _update_skill_dir(skill_dir, entry, clean_assets, previous_assets)
        return read_local_skill(folder, project_root)


def _create_skill_dir(skill_dir: Path, entry: str, assets: Mapping[str, str]) -> None:
    """Fills a hidden sibling, then renames it into place: all or nothing."""
    skill_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = skill_dir.parent / f".{skill_dir.name}.{secrets.token_hex(8)}"
    staging.mkdir()
    try:
        _update_skill_dir(staging, entry, assets, {})
        staging.rename(skill_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _update_skill_dir(
    skill_dir: Path,
    entry: str,
    assets: Mapping[str, str],
    previous_assets: Mapping[str, str],
) -> None:
    """Writes the change, or on any failure puts back every file it touched.

    The version hashes all of them, so a half-applied save would make the
    editor's retry conflict with a skill it never meant to change.
    """
    changed = [
        path for path, content in assets.items() if previous_assets.get(path) != content
    ]
    removed = sorted(previous_assets.keys() - assets.keys())
    touched = [*changed, *removed, ENTRY_FILE]
    before = {
        path: (skill_dir / path).read_bytes() if (skill_dir / path).is_file() else None
        for path in touched
    }
    try:
        # Removed first: on macOS and Windows a case-only rename names the
        # same file twice, and a later unlink would delete the new copy.
        for path in removed:
            (skill_dir / path).unlink(missing_ok=True)
        for path in changed:
            target = skill_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, assets[path])
        _atomic_write(skill_dir / ENTRY_FILE, entry)
    except BaseException:
        _restore(skill_dir, before)
        raise
    for path in removed:
        _remove_empty_parents(skill_dir / path, stop=skill_dir)


def _restore(skill_dir: Path, before: Mapping[str, bytes | None]) -> None:
    for path, content in before.items():
        target = skill_dir / path
        try:
            if content is None:
                if target.is_file():
                    target.unlink()
                    _remove_empty_parents(target, stop=skill_dir)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_bytes(target, content)
        except OSError:
            continue


def delete_local_skill(folder: str, project_root: Path | None) -> None:
    with _WRITE_LOCK, _os_errors():
        skill_dir = _existing_skill_dir(folder, project_root)
        if skill_dir.is_symlink():
            skill_dir.unlink()
        elif skill_dir.is_junction():
            # rmdir drops a Windows junction and leaves its target alone.
            os.rmdir(skill_dir)
        else:
            shutil.rmtree(skill_dir)


def _check_name(name: str) -> None:
    if not name or name in {".", ".."} or PurePosixPath(name).name != name:
        raise LocalSkillError(f"Invalid skill name {name!r}")
    if os.sep in name or (os.altsep and os.altsep in name) or ":" in name:
        raise LocalSkillError(f"Invalid skill name {name!r}")


def _has_skill(folder: str, project_root: Path | None) -> bool:
    try:
        _existing_skill_dir(folder, project_root)
    except LocalSkillNotFound:
        return False
    return True


def _check_case_collisions(assets: Mapping[str, str]) -> None:
    # On macOS and Windows the second write would replace the first.
    seen: dict[str, str] = {}
    for path in assets:
        other = seen.setdefault(path.casefold(), path)
        if other != path:
            raise LocalSkillError(f"{other} and {path} differ only in case")


def _existing_skill_dir(folder: str, project_root: Path | None) -> Path:
    """The folder listing shows under ``folder``; an out-of-project link is skipped."""
    _check_name(folder)
    for root in skill_roots(project_root):
        skill_dir = root / folder
        entry = skill_dir / ENTRY_FILE
        if entry.is_file() and _within_project(entry, project_root):
            return skill_dir
    raise LocalSkillNotFound(f"No skill named {folder!r}")


def _within_project(path: Path, project_root: Path | None) -> bool:
    # A repository's links must not lead out of it; the user's own links are theirs.
    return project_root is None or resolves_within(path, [project_root])


def _in_project(path: Path, project_root: Path | None) -> Path:
    return path if project_root is None else _contained(path, project_root)


def _contained(path: Path, boundary: Path) -> Path:
    if not resolves_within(path, [boundary]):
        raise LocalSkillError(f"{path} leads outside {boundary}")
    return path


@contextmanager
def _os_errors() -> Iterator[None]:
    try:
        yield
    except OSError as exc:
        raise LocalSkillError(str(exc)) from exc


def _skill_name(frontmatter: Mapping[str, Any], folder: str) -> str:
    name = frontmatter.get("name")
    return name if isinstance(name, str) and name else folder


def _description(frontmatter: Mapping[str, Any]) -> str:
    description = frontmatter.get("description")
    return description if isinstance(description, str) else ""


def _asset_path(raw: str) -> str:
    path = PurePosixPath(raw.strip())
    if (
        not path.parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in raw
        or ":" in raw
    ):
        raise LocalSkillError(f"Invalid file path {raw!r}")
    if any(part.startswith(".") for part in path.parts[:-1]):
        raise LocalSkillError(f"{raw!r} is in a hidden folder the editor cannot show")
    clean = path.as_posix()
    if clean.casefold() == ENTRY_FILE.casefold():
        raise LocalSkillError(f"{ENTRY_FILE} is the skill's body, not a file")
    return clean


def _iter_files(skill_dir: Path) -> Iterator[tuple[str, Path]]:
    for root, dirs, files in os.walk(skill_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for file_name in sorted(files):
            full = Path(root) / file_name
            relative = full.relative_to(skill_dir).as_posix()
            if relative == ENTRY_FILE or _LEFTOVER.fullmatch(file_name):
                continue
            yield relative, full


def _read_assets(skill_dir: Path) -> tuple[dict[str, str], list[str]]:
    assets: dict[str, str] = {}
    skipped: list[str] = []
    for relative, full in _iter_files(skill_dir):
        # The editor never follows a link, and a save must not replace one.
        if full.is_symlink() or len(assets) >= MAX_ASSET_COUNT:
            skipped.append(relative)
            continue
        try:
            if full.stat().st_size > MAX_ASSET_BYTES:
                skipped.append(relative)
                continue
            assets[relative] = full.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            skipped.append(relative)
    return assets, skipped


def _version(entry: Path, assets: Mapping[str, str], skipped: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(entry.read_bytes())
    for path in sorted(assets):
        digest.update(b"\0" + path.encode() + b"\0" + assets[path].encode())
    for path in sorted(skipped):
        digest.update(b"\1" + path.encode())
    return f"sha256:{digest.hexdigest()}"


def _render_entry(frontmatter: Mapping[str, Any], body: str) -> str:
    front = yaml.safe_dump(
        dict(frontmatter), default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    text = body.lstrip("\r\n").rstrip()
    return f"---\n{front}---\n\n{text}\n"


def _atomic_write(target: Path, content: str) -> None:
    _atomic_write_bytes(target, content.encode("utf-8"))


def _atomic_write_bytes(target: Path, content: bytes) -> None:
    # Created like any new file (0o666 less the umask), unlike mkstemp's 0o600:
    # a repo skill other users and harnesses read must stay readable.
    tmp = target.parent / f".{target.name}.{secrets.token_hex(8)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        if target.exists():
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _remove_empty_parents(path: Path, *, stop: Path) -> None:
    # Tidying only: the save has already landed, so it must not fail it.
    parent = path.parent
    try:
        while parent != stop and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except OSError:
        return
