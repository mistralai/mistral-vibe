"""Session-less projectLinks controller.

Owns the local-repo <-> Vibe Code Web project link lifecycle for delivery
surfaces. Every method is stateless and keyed on the absolute `root_path` held
by the caller. Responses intentionally carry `repoLocalPath` because this is a
local app-server boundary; renderers can derive compact labels from the basename.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from vibe.core.config import VibeConfigSchema, build_default_orchestrator
from vibe.core.teleport.errors import (
    ServiceTeleportError,
    ServiceTeleportNotSupportedError,
)
from vibe.core.vibe_code_project.client import VibeCodeProjectApiError
from vibe.core.vibe_code_project.picker_service import (
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
    VibeCodeProjectResolverError,
)
from vibe.core.vibe_code_project.project_store import VibeProjectsStore
from vibe.core.vibe_code_project.selection import (
    ProjectPickerContext,
    VibeCodeProject,
    VibeCodeProjectLink,
    normalize_repo_url,
    rank_project_items,
)
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_sentry_exception

if TYPE_CHECKING:
    from vibe.core.teleport.git import GitRepoInfo


class ProjectLinksError(Exception):
    """Base for projectLinks failures mapped by delivery surfaces."""


class ProjectLinksAuthError(ProjectLinksError):
    """Missing/rejected Vibe Code credentials — the user's to fix."""


class ProjectLinksInvalidRequest(ProjectLinksError):
    """The request cannot be satisfied (bad root, unavailable project, ...)."""


class ProjectLinksInternalError(ProjectLinksError):
    """An unexpected failure already reported to Sentry."""


def _resolve_root_reject_reason(
    exc: ServiceTeleportNotSupportedError,
) -> Literal["not_git", "unsupported_remote", "no_commits"]:
    message = str(exc).casefold()
    if "git repository" in message:
        return "not_git"
    # A supported GitHub remote is confirmed before the commit check, so a
    # commit-resolution failure means the repo has no commits yet — not an
    # unsupported remote.
    if "commit" in message:
        return "no_commits"
    return "unsupported_remote"


def _candidate_match_kind(
    project: VibeCodeProject,
) -> Literal["exact_repo", "multi_repo"]:
    return "exact_repo" if len(project.repositories) == 1 else "multi_repo"


def _root_dict(repo_root: Path, git_info: GitRepoInfo) -> dict[str, Any]:
    return {
        "repoLocalPath": str(repo_root),
        "repoName": git_info.repo,
        "currentBranch": git_info.branch,
        "defaultBranch": git_info.default_branch,
    }


def _candidate_page(
    context: ProjectPickerContext,
    state: VibeCodeProjectPickerState,
    *,
    recommend: bool = True,
    saved_project_id: str | None = None,
) -> dict[str, Any]:
    # Reuse the shared ranking (selection.rank_project_items) so Desktop
    # recommends the same project the terminal UI would: currently linked first,
    # then exact-repo matches, then multi-repo matches, each by name.
    ranked = rank_project_items(context=context, projects=state.projects)
    items = [
        {
            "projectId": project.project_id,
            "name": project.name,
            "matchKind": _candidate_match_kind(project),
            "recommended": recommend and index == 0,
        }
        for index, project in enumerate(ranked)
    ]
    # Never recommend a candidate that contradicts the saved link: the saved
    # (currently linked) project ranks first when it is on this page, so a top
    # candidate that isn't it means the saved project is off-page or read-only.
    if (
        saved_project_id is not None
        and items
        and items[0]["projectId"] != saved_project_id
    ):
        items[0]["recommended"] = False
    return {"items": items, "nextCursor": state.next_cursor}


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
        # Local-link metadata is local state, so listing it does not depend on a
        # configured Vibe Code API key.
        store = VibeProjectsStore()
        links = await asyncio.to_thread(store.list_remote_projects)
        groups: dict[str, list[VibeCodeProjectLink]] = {}
        for link in links:
            groups.setdefault(link.project_id, []).append(link)

        projects = [
            self._build_group(project_id, project_links)
            for project_id, project_links in groups.items()
        ]
        return {"projects": projects}

    async def resolve_root(self, root_path: str) -> dict[str, Any]:
        path = Path(root_path).expanduser()
        try:
            git_info = await self._git_info(path)
        except ServiceTeleportNotSupportedError as exc:
            return {
                "eligible": False,
                "rejectReason": _resolve_root_reject_reason(exc),
                "root": None,
            }
        except ServiceTeleportError:
            return {
                "eligible": False,
                "rejectReason": "nested_unresolvable",
                "root": None,
            }

        repo_root = git_info.repo_root or path.resolve()
        return {
            "eligible": True,
            "rejectReason": None,
            "root": _root_dict(repo_root, git_info),
        }

    async def inspect_root(self, root_path: str) -> dict[str, Any]:
        path = Path(root_path).expanduser()
        try:
            git_info = await self._git_info(path)
        except ServiceTeleportNotSupportedError as exc:
            return {
                "eligible": False,
                "rejectReason": _resolve_root_reject_reason(exc),
                "root": None,
                "savedLink": None,
                "staleLinkCleared": False,
            }
        except ServiceTeleportError:
            return {
                "eligible": False,
                "rejectReason": "nested_unresolvable",
                "root": None,
                "savedLink": None,
                "staleLinkCleared": False,
            }

        repo_root = git_info.repo_root or path.resolve()
        if not git_info.remote_url:
            return {
                "eligible": False,
                "rejectReason": "unsupported_remote",
                "root": None,
                "savedLink": None,
                "staleLinkCleared": False,
            }

        store = VibeProjectsStore()
        saved_link = await asyncio.to_thread(
            store.get_remote_project, repo_root=repo_root
        )
        stale_link_cleared = False
        stale_link_clear_failed = False
        saved_link_summary: dict[str, Any] | None = None
        if saved_link is not None:
            if normalize_repo_url(saved_link.repo_url) == normalize_repo_url(
                git_info.remote_url
            ):
                saved_link_summary = {
                    "projectId": saved_link.project_id,
                    "projectName": saved_link.project_name,
                }
            else:
                try:
                    await asyncio.to_thread(
                        store.delete_remote_project, repo_root=repo_root
                    )
                    stale_link_cleared = True
                except Exception as exc:
                    _report_store_delete_failure(exc, "projectLinks/inspectRoot")
                    stale_link_clear_failed = True

        return {
            "eligible": True,
            "rejectReason": None,
            "root": {**_root_dict(repo_root, git_info), "repoUrl": git_info.remote_url},
            "savedLink": saved_link_summary,
            "staleLinkCleared": stale_link_cleared,
            "staleLinkClearFailed": stale_link_clear_failed,
        }

    async def picker_load(self, root_path: str) -> dict[str, Any]:
        repo_root, git_info = await self._resolve_root(root_path)
        service = await self._build_service(repo_root)
        initial_data = await self._load_initial(
            service, git_info, "projectLinks/picker/load"
        )

        context = initial_data.context
        saved_link = context.saved_link
        stale_link_cleared = False
        saved_link_summary: dict[str, Any] | None = None
        if saved_link is not None:
            if normalize_repo_url(saved_link.repo_url) == normalize_repo_url(
                context.repo_url
            ):
                saved_link_summary = {
                    "projectId": saved_link.project_id,
                    "projectName": saved_link.project_name,
                }
            else:
                await asyncio.to_thread(service.clear_project_link, context)
                stale_link_cleared = True

        candidates = _candidate_page(
            context,
            initial_data.state,
            saved_project_id=(
                saved_link_summary["projectId"] if saved_link_summary else None
            ),
        )
        return {
            "root": _root_dict(repo_root, git_info),
            "savedLink": saved_link_summary,
            "staleLinkCleared": stale_link_cleared,
            "candidates": candidates,
        }

    async def picker_load_more(self, root_path: str, cursor: str) -> dict[str, Any]:
        repo_root, git_info = await self._resolve_root(root_path)
        service = await self._build_service(repo_root)
        state = VibeCodeProjectPickerState(
            projects=[], next_cursor=cursor, repo_url=git_info.remote_url
        )
        try:
            result = await service.load_more(state)
        except VibeCodeProjectApiError as exc:
            raise self._api_error(exc, "projectLinks/picker/loadMore") from exc

        # Pagination pages have no saved-link context (the current link, if any,
        # is on the first page), so rank by exact/multi-repo match only and never
        # mark a candidate recommended.
        context = ProjectPickerContext(
            repo_root=repo_root, repo_url=git_info.remote_url, repo_name=git_info.repo
        )
        candidates = _candidate_page(context, result.state, recommend=False)
        return {"candidates": candidates, "focusProjectId": result.focus_project_id}

    async def create(
        self, root_path: str, name: str, default_branch: str
    ) -> dict[str, Any]:
        repo_root, git_info = await self._resolve_root(root_path)
        service = await self._build_service(repo_root)
        initial_data = await self._load_initial(
            service, git_info, "projectLinks/create"
        )
        try:
            create_result = await service.create_project(
                name=name,
                default_branch=default_branch,
                git_info=git_info,
                state=initial_data.state,
            )
        except VibeCodeProjectApiError as exc:
            raise self._api_error(exc, "projectLinks/create") from exc

        project = create_result.project
        link = await asyncio.to_thread(
            service.save_project_link,
            context=initial_data.context,
            project_id=project.project_id,
            project_name=project.name,
        )
        return self._link_dict(link, repo_root)

    async def link(
        self, root_path: str, project_id: str, project_name: str
    ) -> dict[str, Any]:
        del project_name  # server persists the validated project's own name
        repo_root, git_info = await self._resolve_root(root_path)
        service = await self._build_service(repo_root)
        # Validate the target before persisting: a stateless client must not be
        # able to save an arbitrary link.
        try:
            project = await service.find_linkable_project(
                project_id=project_id, repo_url=git_info.remote_url
            )
        except VibeCodeProjectResolverError as exc:
            raise ProjectLinksInvalidRequest(str(exc)) from exc
        except VibeCodeProjectApiError as exc:
            raise self._api_error(exc, "projectLinks/link") from exc
        context = ProjectPickerContext(
            repo_root=repo_root, repo_url=git_info.remote_url, repo_name=git_info.repo
        )
        link = await asyncio.to_thread(
            service.save_project_link,
            context=context,
            project_id=project.project_id,
            project_name=project.name,
        )
        return self._link_dict(link, repo_root)

    async def save(
        self, root_path: str, project_id: str, project_name: str, expected_repo_url: str
    ) -> dict[str, Any]:
        repo_root, git_info = await self._resolve_root(root_path)
        if not git_info.remote_url:
            raise ProjectLinksInvalidRequest(
                "Not an eligible project root: unsupported_remote"
            )
        expected_repo_url = expected_repo_url.strip()
        if not expected_repo_url:
            raise ProjectLinksInvalidRequest("Expected repository URL is required.")
        if normalize_repo_url(git_info.remote_url) != normalize_repo_url(
            expected_repo_url
        ):
            raise ProjectLinksInvalidRequest(
                "The repository remote changed before the link could be saved."
            )

        link = VibeCodeProjectLink(
            repo_root=repo_root,
            repo_url=git_info.remote_url,
            project_id=project_id,
            project_name=project_name,
        )
        store = VibeProjectsStore()
        await asyncio.to_thread(store.upsert_remote_project, link)
        return self._link_dict(link, repo_root)

    async def unlink(self, root_path: str) -> dict[str, Any]:
        # Unlink only needs the store key (repo_root). For a live checkout the
        # git-resolved root matches how the link was stored.
        try:
            repo_root, _ = await self._resolve_root(root_path)
        except ProjectLinksInvalidRequest:
            # Checkout moved/deleted: git resolution fails. Links are keyed on
            # the git-resolved root, which may be an ancestor of the path the
            # user chose (desktop-main can
            # pass a nested dir). Clear the stored link whose root contains the
            # chosen path so a stranded link can still be removed.
            return await asyncio.to_thread(self._unlink_stale_root, root_path)
        store = VibeProjectsStore()
        try:
            await asyncio.to_thread(store.delete_remote_project, repo_root=repo_root)
        except Exception as exc:
            _report_store_delete_failure(exc, "projectLinks/unlink")
        return {"unlinked": True}

    @staticmethod
    def _unlink_stale_root(root_path: str) -> dict[str, Any]:
        raw = Path(root_path).expanduser().resolve()
        store = VibeProjectsStore()
        # list_remote_projects() returns resolved repo_root paths, so both sides
        # are normalized before comparison.
        candidates = [
            link
            for link in store.list_remote_projects()
            if raw == link.repo_root or raw.is_relative_to(link.repo_root)
        ]
        if candidates:
            # Closest (deepest) ancestor wins when projects are nested.
            target = max(candidates, key=lambda link: len(link.repo_root.parts))
            try:
                store.delete_remote_project(repo_root=target.repo_root)
            except Exception as exc:
                _report_store_delete_failure(exc, "projectLinks/unlink")
        return {"unlinked": True}

    # -- internals -------------------------------------------------------------

    async def _load_config(self) -> VibeConfigSchema:
        orchestrator = await build_default_orchestrator(require_api_key=False)
        return orchestrator.config

    async def _build_service(self, repo_root: Path) -> VibeCodeProjectPickerService:
        config = await self._load_config()
        api_key = config.vibe_code_api_key
        if not api_key:
            raise ProjectLinksAuthError(f"{config.vibe_code_api_key_env_var} not set.")
        return VibeCodeProjectPickerService(
            base_url=config.vibe_code_sessions_base_url,
            api_key=api_key,
            repo_root=repo_root,
            project_store=VibeProjectsStore(),
            timeout=config.api_timeout,
        )

    async def _git_info(self, repo_root: Path) -> GitRepoInfo:
        # Imported lazily so lightweight delivery-surface imports do not pull in gitpython.
        from vibe.core.teleport.git import GitRepository

        async with GitRepository(workdir=repo_root) as git:
            return await git.get_metadata()

    async def _resolve_root(self, root_path: str) -> tuple[Path, GitRepoInfo]:
        path = Path(root_path).expanduser()
        try:
            git_info = await self._git_info(path)
        except ServiceTeleportError as exc:
            raise ProjectLinksInvalidRequest(
                f"Not an eligible project root: {exc}"
            ) from exc
        repo_root = git_info.repo_root or path.resolve()
        return repo_root, git_info

    async def _load_initial(
        self,
        service: VibeCodeProjectPickerService,
        git_info: GitRepoInfo,
        boundary_method: str,
    ) -> VibeCodeProjectPickerInitialData:
        try:
            return await service.load_initial(git_info)
        except VibeCodeProjectApiError as exc:
            raise self._api_error(exc, boundary_method) from exc

    def _build_group(
        self, project_id: str, project_links: list[VibeCodeProjectLink]
    ) -> dict[str, Any]:
        # The Desktop loopback folds these onto the project's `repositories` as
        # local-checkout entries; the absolute path is the display value.
        return {
            "projectId": project_id,
            "repoLocalPaths": [str(link.repo_root) for link in project_links],
        }

    @staticmethod
    def _link_dict(link: VibeCodeProjectLink, repo_root: Path) -> dict[str, Any]:
        return {
            "link": {
                "projectId": link.project_id,
                "projectName": link.project_name,
                "repoLocalPath": str(repo_root),
            }
        }

    @staticmethod
    def _api_error(
        exc: VibeCodeProjectApiError, boundary_method: str
    ) -> ProjectLinksError:
        lowered = str(exc).casefold()
        # Authentication failures (missing key or an HTTP 401/403 from VCW) are
        # the user's to fix, not internal errors — surface them as auth errors so
        # Desktop shows an auth message and we don't page on them via Sentry.
        if "api key" in lowered or "status 401" in lowered or "status 403" in lowered:
            return ProjectLinksAuthError(
                f"Vibe Code authentication failed ({boundary_method})"
            )
        # Non-auth VCW errors are built from raw response text; forwarding the raw
        # exception to Sentry (or its message to clients) would leak
        # unredacted remote payloads into telemetry. Capture a sanitized error
        # carrying only the boundary/method/type, and return a generic message.
        sanitized = ProjectLinksInternalError(
            f"Vibe Code API request failed ({boundary_method})"
        )
        capture_sentry_exception(
            sanitized,
            fatal=False,
            tags={
                "vibe_boundary": "project_links",
                "project_links_method": boundary_method,
                "error_type": type(exc).__name__,
            },
        )
        return sanitized
