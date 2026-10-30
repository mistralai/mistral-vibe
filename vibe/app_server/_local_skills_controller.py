"""The ``skills/local/*`` API: skill folders edited from Desktop's Skills page.

A skill is addressed on the wire by its folder name; whether it is on is
decided, and toggled, by the name sessions know it by.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._local_skills import (
    MAX_SKIPPED_PATHS,
    LocalSkillConflict,
    LocalSkillEntry,
    LocalSkillError,
    LocalSkillFiles,
    LocalSkillNotFound,
    delete_local_skill,
    list_local_skills,
    read_local_skill,
    write_local_skill,
)
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._projection import writable_disabled_skills
from vibe.app_server._skill_toggles import SkillToggles, set_skill_enabled
from vibe.app_server.protocol import (
    EmptyResponse,
    LocalSkillRefusalData,
    LocalSkillSummary,
    LocalSkillView,
    ProtocolErrorCode,
    SkillsLocalDeleteParams,
    SkillsLocalListParams,
    SkillsLocalListResponse,
    SkillsLocalReadParams,
    SkillsLocalReadResponse,
    SkillsLocalSetEnabledParams,
    SkillsLocalSetEnabledResponse,
    SkillsLocalWriteParams,
    SkillsLocalWriteResponse,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.registry import pinned_names_sync
from vibe.observability.logging import logger

__all__ = ["LOCAL_SKILL_METHODS", "LocalSkillsController"]

LOCAL_SKILL_METHODS = frozenset({
    "skills/local/delete",
    "skills/local/list",
    "skills/local/read",
    "skills/local/setEnabled",
    "skills/local/write",
})

type SessionFiles = Callable[[Path | None], HarnessFilesManager]
type OrchestratorLoader = Callable[
    [HarnessFilesManager], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
]


class LocalSkillsController:
    def __init__(
        self, session_files: SessionFiles, load_orchestrator: OrchestratorLoader
    ) -> None:
        """``session_files(None)`` is the user's own config, the one toggles write."""
        self._session_files = session_files
        self._load_orchestrator = load_orchestrator

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        try:
            match method:
                case "skills/local/list":
                    return await self._list(
                        validate_wire(SkillsLocalListParams, raw_params)
                    )
                case "skills/local/read":
                    return await self._read(
                        validate_wire(SkillsLocalReadParams, raw_params)
                    )
                case "skills/local/write":
                    return await self._write(
                        validate_wire(SkillsLocalWriteParams, raw_params)
                    )
                case "skills/local/setEnabled":
                    return await self._set_enabled(
                        validate_wire(SkillsLocalSetEnabledParams, raw_params)
                    )
                case "skills/local/delete":
                    return await self._delete(
                        validate_wire(SkillsLocalDeleteParams, raw_params)
                    )
                case _:
                    raise method_not_found(method)
        except LocalSkillNotFound as exc:
            raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        except LocalSkillConflict as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except LocalSkillError as exc:
            data = (
                None
                if exc.reason is None
                else LocalSkillRefusalData(reason=exc.reason).model_dump(mode="json")
            )
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS, str(exc), data
            ) from exc
        except ValidationError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    async def _list(self, params: SkillsLocalListParams) -> SkillsLocalListResponse:
        project_root = _project_root(params.project_root)
        entries = await asyncio.to_thread(list_local_skills, project_root)
        toggles = await self._toggles(project_root, params.trust_workspace)
        return SkillsLocalListResponse(
            skills=[_summary(entry, toggles) for entry in entries]
        )

    async def _read(self, params: SkillsLocalReadParams) -> SkillsLocalReadResponse:
        project_root = _project_root(params.project_root)
        skill = await asyncio.to_thread(read_local_skill, params.name, project_root)
        return SkillsLocalReadResponse(
            skill=_view(
                skill, await self._toggles(project_root, params.trust_workspace)
            )
        )

    async def _write(self, params: SkillsLocalWriteParams) -> SkillsLocalWriteResponse:
        project_root = _project_root(params.project_root)
        skill = await asyncio.to_thread(
            lambda: write_local_skill(
                folder=params.name,
                description=params.description,
                body=params.body,
                assets=params.assets,
                expected_version=params.expected_version,
                project_root=project_root,
            )
        )
        toggles = await self._toggles(project_root, params.trust_workspace)
        enabled = params.enabled
        if (
            params.expected_version is not None
            or enabled is None
            or toggles.enabled(skill.name) == enabled
            or toggles.locked(skill.name)
        ):
            return SkillsLocalWriteResponse(skill=_view(skill, toggles))
        # The skill is written by now: a failed toggle must not fail its create.
        try:
            await set_skill_enabled(
                await self._load_orchestrator(self._session_files(None)),
                skill.name,
                enabled,
                reason="create a local skill from the Skills page",
            )
        except Exception:
            logger.warning("Failed to set a new local skill's on/off", exc_info=True)
        toggles = await self._toggles(project_root, params.trust_workspace)
        return SkillsLocalWriteResponse(skill=_view(skill, toggles))

    async def _set_enabled(
        self, params: SkillsLocalSetEnabledParams
    ) -> SkillsLocalSetEnabledResponse:
        project_root = _project_root(params.project_root)
        entries = await asyncio.to_thread(list_local_skills, project_root)
        entry = next((e for e in entries if e.folder == params.name), None)
        if entry is None:
            raise LocalSkillNotFound(f"No skill named {params.name!r}")
        toggles = await self._toggles(project_root, params.trust_workspace)
        if toggles.locked(entry.name):
            raise LocalSkillError(
                f"{entry.name!r} is fixed by configuration and cannot be toggled here",
                "locked",
            )
        await set_skill_enabled(
            await self._load_orchestrator(self._session_files(None)),
            entry.name,
            params.enabled,
            reason="toggle a local skill from the Skills page",
        )
        return SkillsLocalSetEnabledResponse(
            skill=_summary(
                entry, await self._toggles(project_root, params.trust_workspace)
            )
        )

    async def _delete(self, params: SkillsLocalDeleteParams) -> EmptyResponse:
        """Also forgets the skill's "off", so one created later under its name starts on.

        Kept while sessions can still load a skill of that name, since the
        toggle is shared by name: from the user's folders, a checkout the
        caller names, the user's ``skill_paths``, or a registry pin. A
        project's own ``skill_paths`` are not read.
        """
        project_root = _project_root(params.project_root)
        other_roots = _existing_roots(params.other_project_roots)
        entries = await asyncio.to_thread(list_local_skills, project_root)
        entry = next((e for e in entries if e.folder == params.name), None)
        await asyncio.to_thread(delete_local_skill, params.name, project_root)
        if entry is None:
            return EmptyResponse()
        orchestrator = await self._load_orchestrator(self._session_files(None))
        if entry.name not in (writable_disabled_skills(orchestrator) or []):
            return EmptyResponse()
        try:
            remaining = await asyncio.to_thread(
                _remaining_names, project_root, other_roots, orchestrator.config
            )
        except OSError:
            # The folder is gone already; an unread `skill_paths` may hold a namesake.
            logger.warning("Kept a deleted local skill's off", exc_info=True)
            return EmptyResponse()
        if entry.name not in remaining:
            await set_skill_enabled(
                orchestrator,
                entry.name,
                True,
                reason="forget a deleted local skill's toggle",
            )
        return EmptyResponse()

    async def _toggles(
        self, project_root: Path | None, trust_workspace: bool
    ) -> SkillToggles:
        """Only names the user layer itself lists can be turned back on here.

        For a project's skill, the config a session there runs with: its own
        layers apply when the caller trusts it, as for the sessions it opens.
        """
        files = self._session_files(project_root)
        if project_root is not None and trust_workspace:
            files = replace(files, trust_store=files.trust_store.for_session())
            files.trust_store.trust_for_session(project_root)
        orchestrator = await self._load_orchestrator(files)
        config = orchestrator.config
        return SkillToggles(
            allowed=list(config.enabled_skills),
            disabled=list(config.disabled_skills),
            own=list(writable_disabled_skills(orchestrator) or []),
        )


def _remaining_names(
    project_root: Path | None, other_roots: list[Path], config: VibeConfigSchema
) -> set[str]:
    roots = {None, project_root, *other_roots}
    names: set[str] = set()
    for root in roots:
        try:
            names.update(entry.name for entry in list_local_skills(root))
        except LocalSkillError:
            continue
    # With no sources, the manager reads only the configured `skill_paths`.
    configured = SkillManager(
        lambda: config, harness_files=HarnessFilesManager(sources=())
    )
    names.update(skill.name for skill in configured.installed_skills())
    # Pins count before they are downloaded: sessions fetch them later.
    if config.experimental_enable_registry_skills:
        names |= pinned_names_sync([root for root in roots if root is not None])
    return names


def _existing_roots(values: list[str]) -> list[Path]:
    """A moved or deleted checkout simply has no skills left."""
    roots = [Path(value).expanduser() for value in values]
    return [root.resolve() for root in roots if root.is_absolute() and root.is_dir()]


def _project_root(value: str | None) -> Path | None:
    if value is None:
        return None
    root = Path(value).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise LocalSkillError(
            f"Not a project folder: {value!r}", "not_a_project_folder"
        )
    return root.resolve()


def _summary(entry: LocalSkillEntry, toggles: SkillToggles) -> LocalSkillSummary:
    return LocalSkillSummary(
        name=entry.folder,
        skill_name=entry.name,
        description=entry.description,
        path=str(entry.path),
        enabled=toggles.enabled(entry.name),
        locked=toggles.locked(entry.name),
    )


def _view(skill: LocalSkillFiles, toggles: SkillToggles) -> LocalSkillView:
    return LocalSkillView(
        name=skill.folder,
        skill_name=skill.name,
        description=skill.description,
        body=skill.body,
        assets=skill.assets,
        skipped_paths=skill.skipped_paths[:MAX_SKIPPED_PATHS],
        path=str(skill.path),
        version=skill.version,
        enabled=toggles.enabled(skill.name),
        locked=toggles.locked(skill.name),
    )
