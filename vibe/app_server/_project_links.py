"""Session-less projectLinks controller.

Owns the local-directory <-> Vibe Code Web project link lifecycle for delivery
surfaces. Every method is stateless and keyed on the absolute `root_path` held
by the caller. Git metadata is optional.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibe.core.git.errors import (
    GitError,
    GitRepositoryError,
    GitRepositoryNotFoundError,
    GitUnavailableError,
)
from vibe.core.git.remote import to_https_url
from vibe.core.git.repo import GitRepo
from vibe.core.vibe_code_project.project_store import VibeProjectsStore
from vibe.core.vibe_code_project.selection import (
    LocalProjectLink,
    ProjectLink,
    RemoteProjectLink,
    project_link_path,
)
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_sentry_exception
from vibe.utils.repository import normalize_repo_url

if TYPE_CHECKING:
    from vibe.app_server.protocol import ProjectLinksResolveRootRejectReason


@dataclass(frozen=True, slots=True)
class _DirectoryGitInfo:
    github_repo_url: str | None
    current_branch: str | None
    default_branch: str | None
    has_commits: bool


@dataclass(frozen=True, slots=True)
class _InspectedDirectory:
    path: Path
    name: str
    git: _DirectoryGitInfo | None


class ProjectLinksError(Exception):
    """Base for projectLinks failures mapped by delivery surfaces."""


class ProjectLinksInvalidRequest(ProjectLinksError):
    """The request cannot be satisfied (bad root, unavailable project, ...)."""


# A GitRepositoryError so that callers mapping inspection failures to an
# invalid request keep doing so; only the reject reason tells it apart.
class _GitUnavailableInCheckout(GitRepositoryError):
    pass


def _reject_reason(error: GitRepositoryError) -> ProjectLinksResolveRootRejectReason:
    if isinstance(error, _GitUnavailableInCheckout):
        return "git_unavailable"
    return "nested_unresolvable"


def _is_inside_git_checkout(directory: Path) -> bool:
    return any(
        os.path.lexists(candidate / ".git")
        for candidate in (directory, *directory.parents)
    )


def _inspection_dict(inspected: _InspectedDirectory) -> dict[str, Any]:
    return {
        "directoryPath": str(inspected.path),
        "directoryName": inspected.name,
        "git": (
            {
                "currentBranch": inspected.git.current_branch,
                "defaultBranch": inspected.git.default_branch,
                "githubRepoUrl": inspected.git.github_repo_url,
                "hasCommits": inspected.git.has_commits,
            }
            if inspected.git is not None
            else None
        ),
    }


def _read_inspected_directory(path: Path) -> _InspectedDirectory:
    try:
        directory_path = path.resolve(strict=True)
    except OSError as exc:
        raise GitRepositoryError(str(exc)) from exc
    if not directory_path.is_dir():
        raise GitRepositoryError("Not a directory")

    plain_directory = _InspectedDirectory(
        path=directory_path, name=directory_path.name, git=None
    )
    try:
        repository = GitRepo.open(directory_path)
        with repository:
            status = repository.status()
            github_remote = repository.github_remote()
            has_commits = repository.has_commits()
    except GitRepositoryNotFoundError:
        return plain_directory
    except GitUnavailableError as exc:
        # Git is optional for a directory outside any checkout, which links as
        # a plain one either way. A checkout's link needs what git reports.
        if _is_inside_git_checkout(directory_path):
            raise _GitUnavailableInCheckout(str(exc)) from exc
        return plain_directory
    except GitError as exc:
        raise GitRepositoryError(str(exc)) from exc

    repo_root = status.root.resolve()
    return _InspectedDirectory(
        path=repo_root,
        name=repo_root.name,
        git=_DirectoryGitInfo(
            github_repo_url=(
                to_https_url(github_remote.owner, github_remote.repo)
                if github_remote is not None
                else None
            ),
            current_branch=status.branch,
            default_branch=status.base_branch,
            has_commits=has_commits,
        ),
    )


def _report_store_delete_failure(exc: Exception, boundary_method: str) -> None:
    logger.warning(
        "Failed to delete project link while handling %s: %s", boundary_method, exc
    )
    capture_sentry_exception(
        exc,
        fatal=False,
        tags={
            "vibe_boundary": "project_links",
            "project_links_method": boundary_method,
            "error_type": type(exc).__name__,
        },
    )


class ProjectLinksController:
    """Stateless projectLinks operations, resolved per absolute `root_path`."""

    async def list_links(self) -> dict[str, Any]:
        store = VibeProjectsStore()
        links = await asyncio.to_thread(store.list_project_links)
        groups: dict[str, list[ProjectLink]] = {}
        for link in links:
            groups.setdefault(link.project_id, []).append(link)

        projects = await asyncio.gather(
            *(
                asyncio.to_thread(self._build_group, project_id, project_links)
                for project_id, project_links in groups.items()
            )
        )
        return {"projects": projects}

    async def resolve_root(self, root_path: str) -> dict[str, Any]:
        path = Path(root_path).expanduser()
        try:
            inspected = await self._inspect_directory(path)
        except GitRepositoryError as exc:
            return {
                "eligible": False,
                "rejectReason": _reject_reason(exc),
                "root": None,
            }

        return {
            "eligible": True,
            "rejectReason": None,
            "root": _inspection_dict(inspected),
        }

    async def inspect_root(self, root_path: str) -> dict[str, Any]:
        path = Path(root_path).expanduser()
        try:
            inspected = await self._inspect_directory(path)
        except GitRepositoryError as exc:
            return {
                "eligible": False,
                "rejectReason": _reject_reason(exc),
                "root": None,
                "savedLink": None,
                "staleLinkCleared": False,
            }

        store = VibeProjectsStore()
        saved_link = await asyncio.to_thread(
            store.get_project_link, repo_root=inspected.path
        )
        stale_link_cleared = False
        stale_link_clear_failed = False
        saved_link_summary: dict[str, Any] | None = None
        if saved_link is not None:
            github_repo_url = (
                inspected.git.github_repo_url if inspected.git is not None else None
            )
            link_matches = isinstance(saved_link, LocalProjectLink) or (
                github_repo_url is not None
                and normalize_repo_url(saved_link.repo_url)
                == normalize_repo_url(github_repo_url)
            )
            if link_matches:
                saved_link_summary = {
                    "projectId": saved_link.project_id,
                    "projectName": saved_link.project_name,
                }
            else:
                try:
                    await asyncio.to_thread(
                        store.delete_project_link, repo_root=inspected.path
                    )
                    stale_link_cleared = True
                except Exception as exc:
                    _report_store_delete_failure(exc, "projectLinks/inspectRoot")
                    stale_link_clear_failed = True

        return {
            "eligible": True,
            "rejectReason": None,
            "root": _inspection_dict(inspected),
            "savedLink": saved_link_summary,
            "staleLinkCleared": stale_link_cleared,
            "staleLinkClearFailed": stale_link_clear_failed,
        }

    async def save(
        self,
        root_path: str,
        project_id: str,
        project_name: str,
        expected_github_repo_url: str | None,
    ) -> dict[str, Any]:
        inspected = await self._resolve_inspection_root(root_path)
        expected_github_repo_url = (
            expected_github_repo_url.strip()
            if expected_github_repo_url is not None
            else None
        )
        github_repo_url = (
            inspected.git.github_repo_url if inspected.git is not None else None
        )

        link: ProjectLink
        if github_repo_url is None:
            if expected_github_repo_url is not None:
                raise ProjectLinksInvalidRequest(
                    "The repository remote changed before the link could be saved."
                )
            link = LocalProjectLink(
                directory_path=inspected.path,
                project_id=project_id,
                project_name=project_name,
            )
        else:
            if expected_github_repo_url is None or normalize_repo_url(
                github_repo_url
            ) != normalize_repo_url(expected_github_repo_url):
                raise ProjectLinksInvalidRequest(
                    "The repository remote changed before the link could be saved."
                )
            link = RemoteProjectLink(
                repo_root=inspected.path,
                repo_url=github_repo_url,
                project_id=project_id,
                project_name=project_name,
            )

        store = VibeProjectsStore()
        await asyncio.to_thread(store.upsert_project_link, link)
        return self._link_dict(link, inspected.path)

    async def unlink(self, root_path: str) -> dict[str, Any]:
        # Unlink only needs the store key (repo_root). For a live checkout the
        # git-resolved root matches how the link was stored.
        try:
            inspected = await self._resolve_inspection_root(root_path)
        except ProjectLinksInvalidRequest:
            # Checkout moved/deleted: git resolution fails. Links are keyed on
            # the git-resolved root, which may be an ancestor of the path the
            # user chose (desktop-main can
            # pass a nested dir). Clear the stored link whose root contains the
            # chosen path so a stranded link can still be removed.
            return await asyncio.to_thread(self._unlink_stale_root, root_path)
        store = VibeProjectsStore()
        try:
            await asyncio.to_thread(store.delete_project_link, repo_root=inspected.path)
        except Exception as exc:
            _report_store_delete_failure(exc, "projectLinks/unlink")
        return {"unlinked": True}

    @staticmethod
    def _unlink_stale_root(root_path: str) -> dict[str, Any]:
        raw = Path(root_path).expanduser().resolve()
        store = VibeProjectsStore()
        # list_project_links() returns resolved paths, so both sides
        # are normalized before comparison.
        candidates = [
            link
            for link in store.list_project_links()
            if raw == project_link_path(link)
            or raw.is_relative_to(project_link_path(link))
        ]
        if candidates:
            # Closest (deepest) ancestor wins when projects are nested.
            target = max(
                candidates, key=lambda link: len(project_link_path(link).parts)
            )
            try:
                store.delete_project_link(repo_root=project_link_path(target))
            except Exception as exc:
                _report_store_delete_failure(exc, "projectLinks/unlink")
        return {"unlinked": True}

    # -- internals -------------------------------------------------------------

    async def _inspect_directory(self, path: Path) -> _InspectedDirectory:
        return await asyncio.to_thread(_read_inspected_directory, path)

    async def _resolve_inspection_root(self, root_path: str) -> _InspectedDirectory:
        path = Path(root_path).expanduser()
        try:
            inspected = await self._inspect_directory(path)
        except GitRepositoryError as exc:
            raise ProjectLinksInvalidRequest(
                f"Not an eligible project root: {exc}"
            ) from exc
        return inspected

    def _build_group(
        self, project_id: str, project_links: list[ProjectLink]
    ) -> dict[str, Any]:
        # The Desktop loopback folds these onto the project's `repositories` as
        # local-directory entries; the absolute path is the display value.
        return {
            "projectId": project_id,
            "localLinks": [self._local_link_dict(link) for link in project_links],
        }

    @staticmethod
    def _local_link_dict(link: ProjectLink) -> dict[str, Any]:
        path = project_link_path(link)
        try:
            with GitRepo.open(path) as repository:
                has_commits = repository.has_commits()
        except GitError:
            has_commits = False
        return {"directoryPath": str(path), "hasCommits": has_commits}

    @staticmethod
    def _link_dict(link: ProjectLink, directory_path: Path) -> dict[str, Any]:
        return {
            "link": {
                "projectId": link.project_id,
                "projectName": link.project_name,
                "directoryPath": str(directory_path),
            }
        }
