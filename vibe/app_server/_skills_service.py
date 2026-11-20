"""The ``skills/*`` API, written once for both session backends.

Every method below is backend-agnostic: it reads the layered config, talks to
the skill registry, and projects the result onto the wire. The four things that
*do* differ between the legacy agent loop and the Unified Harness — where the
config comes from, what counts as the skill roots, how a session is refreshed
after a mutation, and how the catalogue is projected — are the members of
``SkillsHost``. Adding a ``skills/*`` method here reaches both backends; adding
one to a backend reaches half the users.

This follows ``IdentityController``/``IdentityHost``: the controller owns the
behaviour, the host owns the session.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._local_skills import (
    ENTRY_FILE,
    delete_skill_folder,
    listed_skill_folder,
)
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._skill_toggles import set_skill_enabled
from vibe.app_server.models import (
    SkillCatalogEntry,
    SkillDetailView,
    SkillSummary,
    SkillUpdateView,
    SkillVersionView,
)
from vibe.app_server.protocol import (
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeSnapshot,
    SkillsCatalogParams,
    SkillsCatalogResponse,
    SkillsConvertLocalParams,
    SkillsConvertResponse,
    SkillsDeleteLocalParams,
    SkillsDetailParams,
    SkillsDetailResponse,
    SkillsImportParams,
    SkillsInstalledParams,
    SkillsInstalledResponse,
    SkillsListParams,
    SkillsListResponse,
    SkillsRemoveParams,
    SkillsSessionImportResponse,
    SkillsSetAliasParams,
    SkillsSetEnabledParams,
    SkillsSetLatestParams,
    SkillsSetVersionParams,
    SkillsUpdatesParams,
    SkillsUpdatesResponse,
    SkillsVersionsParams,
    SkillsVersionsResponse,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.platform_auth import PlatformCredentialFactory
from vibe.core.skills.models import ManifestEntry, SkillScope
from vibe.core.skills.registry import (
    RegistrySkillsError,
    check_new_versions,
    check_updates,
    convert_skill_to_local,
    get_skill_body,
    get_skill_details,
    has_registry_endpoint,
    import_session_skill,
    import_skill,
    list_catalog,
    list_skill_versions,
    project_scope_available,
    remove_skill,
    set_skill_alias,
    set_skill_latest,
    set_skill_version,
)
from vibe.observability.logging import logger

__all__ = ["SkillsController", "SkillsHost"]


class SkillsHost(Protocol):
    """What ``SkillsController`` needs of its session.

    ``ResourceRequestHandler`` satisfies it from its ``AgentLoop``; the Unified
    backend satisfies it from its session context, so both backends reach one
    implementation.
    """

    @property
    def config(self) -> VibeConfigSchema:
        """The layered config the registry calls read."""
        ...

    @property
    def config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        """The orchestrator a skill toggle persists ``disabled_skills`` through."""
        ...

    @property
    def credentials(self) -> PlatformCredentialFactory | None:
        """The connection's platform credential the registry calls carry."""
        ...

    @property
    def skill_roots(self) -> list[Path]:
        """Project roots a scoped install may write into."""
        ...

    @property
    def local_skill_dirs(self) -> list[Path]:
        """The folders local skills are read from, as configured, links unresolved."""
        ...

    @property
    def sandboxed(self) -> bool:
        """A sandboxed session runs only the skills copied in as it opened."""
        ...

    def require_no_session_rewrite(self) -> None:
        """Raise while a compaction or a relocation rewrites the session."""
        ...

    def require_session(self, session_id: str) -> None:
        """Raise if ``session_id`` is not this session."""
        ...

    def list_skills(self) -> list[SkillSummary]:
        """The catalogue behind ``/skill-name``, de-duped, project-wins."""
        ...

    def installed_skills(self) -> SkillsInstalledResponse:
        """Rows for the browser: one per name, scope and source, each marked
        active when its name resolves to it, and the skills that failed to load.
        """
        ...

    @property
    def session_skill_pins(self) -> tuple[ManifestEntry, ...]:
        """The registry skills picked for this session only."""
        ...

    async def save_session_skill_pins(self, pins: tuple[ManifestEntry, ...]) -> None:
        """Keep ``pins`` for the session and its next open; ``refresh`` applies them."""
        ...

    async def refresh(self) -> RuntimeMutationResponse:
        """Re-read the workspace after a mutation and return the new snapshot.

        During a turn the change is held until the turn ends: ``status`` is then
        ``PENDING``, and ``runtime`` already shows the change.
        """
        ...


def _loaded_pick(runtime: RuntimeSnapshot, entry: ManifestEntry) -> str | None:
    return next(
        (
            skill.name
            for skill in runtime.skills
            if skill.scope == "session"
            and skill.registry is not None
            and skill.registry.skill_id == entry.skill_id
            and skill.registry.version == entry.version
        ),
        None,
    )


class SkillsController:
    def __init__(self, host: SkillsHost) -> None:
        self._host = host

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        match method:
            case "skills/list":
                response: ProtocolModel = self._list(
                    validate_wire(SkillsListParams, raw_params)
                )
                runtime_updated = False
            case "skills/installed":
                response = self._installed(
                    validate_wire(SkillsInstalledParams, raw_params)
                )
                runtime_updated = False
            case "skills/catalog":
                response = await self._catalog(
                    validate_wire(SkillsCatalogParams, raw_params)
                )
                runtime_updated = False
            case "skills/versions":
                response = await self._versions(
                    validate_wire(SkillsVersionsParams, raw_params)
                )
                runtime_updated = False
            case "skills/updates":
                response = await self._updates(
                    validate_wire(SkillsUpdatesParams, raw_params)
                )
                runtime_updated = False
            case "skills/detail":
                response = await self._detail(
                    validate_wire(SkillsDetailParams, raw_params)
                )
                runtime_updated = False
            case "skills/import":
                response = await self._import(
                    validate_wire(SkillsImportParams, raw_params)
                )
                runtime_updated = True
            case "skills/setVersion":
                response = await self._set_version(
                    validate_wire(SkillsSetVersionParams, raw_params)
                )
                runtime_updated = True
            case "skills/setLatest":
                response = await self._set_latest(
                    validate_wire(SkillsSetLatestParams, raw_params)
                )
                runtime_updated = True
            case "skills/setAlias":
                response = await self._set_alias(
                    validate_wire(SkillsSetAliasParams, raw_params)
                )
                runtime_updated = True
            case "skills/remove":
                response = await self._remove(
                    validate_wire(SkillsRemoveParams, raw_params)
                )
                runtime_updated = True
            case "skills/convertLocal":
                response = await self._convert_local(
                    validate_wire(SkillsConvertLocalParams, raw_params)
                )
                runtime_updated = True
            case "skills/deleteLocal":
                response = await self._delete_local(
                    validate_wire(SkillsDeleteLocalParams, raw_params)
                )
                runtime_updated = True
            case "skills/setEnabled":
                response = await self._set_enabled(
                    validate_wire(SkillsSetEnabledParams, raw_params)
                )
                runtime_updated = True
            case _:
                raise method_not_found(method)
        return DispatchResult(response, runtime_updated=runtime_updated)

    def _list(self, params: SkillsListParams) -> SkillsListResponse:
        if params.session_id is None:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS, "skills/list needs a sessionId here"
            )
        self._host.require_session(params.session_id)
        return SkillsListResponse(skills=self._host.list_skills())

    def _installed(self, params: SkillsInstalledParams) -> SkillsInstalledResponse:
        self._host.require_session(params.session_id)
        return self._host.installed_skills()

    async def _catalog(self, params: SkillsCatalogParams) -> SkillsCatalogResponse:
        self._host.require_session(params.session_id)
        config = self._host.config
        roots = self._host.skill_roots
        project_available = project_scope_available(roots)
        credentials = self._host.credentials
        authenticated = await has_registry_endpoint(config, credentials=credentials)
        if not authenticated:
            return SkillsCatalogResponse(
                skills=[],
                updates={},
                loaded=True,
                project_available=project_available,
                authenticated=False,
            )
        try:
            catalog = await list_catalog(config, credentials=credentials)
            updates = {
                u.name: u.latest_version
                for u in await check_updates(config, roots, credentials=credentials)
            }
        except Exception:
            return SkillsCatalogResponse(
                skills=[], updates={}, loaded=False, project_available=project_available
            )
        return SkillsCatalogResponse(
            skills=[
                SkillCatalogEntry(
                    name=c.name,
                    skill_id=c.skill_id,
                    description=c.description,
                    latest_version=c.latest_version,
                    sharing_scope=c.sharing_scope,
                )
                for c in catalog
            ],
            updates=updates,
            loaded=True,
            project_available=project_available,
        )

    async def _versions(self, params: SkillsVersionsParams) -> SkillsVersionsResponse:
        self._host.require_session(params.session_id)
        versions = await list_skill_versions(
            self._host.config, params.skill_id, credentials=self._host.credentials
        )
        return SkillsVersionsResponse(
            versions=[
                SkillVersionView(version=v.version, aliases=list(v.aliases))
                for v in versions
            ]
        )

    async def _updates(self, params: SkillsUpdatesParams) -> SkillsUpdatesResponse:
        self._host.require_session(params.session_id)
        updates = await check_new_versions(
            self._host.config,
            self._host.skill_roots,
            credentials=self._host.credentials,
        )
        return SkillsUpdatesResponse(
            updates=[
                SkillUpdateView(
                    name=u.name,
                    current_version=u.current_version,
                    latest_version=u.latest_version,
                )
                for u in updates
            ]
        )

    async def _detail(self, params: SkillsDetailParams) -> SkillsDetailResponse:
        self._host.require_session(params.session_id)
        config = self._host.config
        credentials = self._host.credentials
        detail = await get_skill_details(
            config, params.skill_id, version=params.version, credentials=credentials
        )
        if detail is not None:
            return SkillsDetailResponse(
                detail=SkillDetailView.model_validate(detail.model_dump())
            )
        try:
            body = await get_skill_body(
                config, params.skill_id, version=params.version, credentials=credentials
            )
        except RegistrySkillsError:
            body = None
        return SkillsDetailResponse(detail=None, body=body)

    async def _import(self, params: SkillsImportParams) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        if params.scope == "session":
            return await self._import_for_session(params)
        try:
            await import_skill(
                self._host.config,
                params.skill_id,
                version=params.version,
                alias=params.alias,
                scope=self._scope(params.scope),
                roots=self._host.skill_roots,
                credentials=self._host.credentials,
            )
        except RegistrySkillsError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, exc.reason) from exc
        return await self._refreshed()

    async def _import_for_session(
        self, params: SkillsImportParams
    ) -> SkillsSessionImportResponse:
        if self._host.sandboxed:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                "a sandboxed session cannot pick a skill for itself",
            )
        try:
            entry = await import_session_skill(
                self._host.config,
                params.skill_id,
                version=params.version,
                alias=params.alias,
                credentials=self._host.credentials,
            )
        except RegistrySkillsError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, exc.reason) from exc
        before = self._host.session_skill_pins
        kept = tuple(pin for pin in before if pin.skill_id != entry.skill_id)
        await self._host.save_session_skill_pins((*kept, entry))
        try:
            refreshed = await self._host.refresh()
            loaded = _loaded_pick(refreshed.runtime, entry)
            if loaded is None:
                raise RequestFailure(
                    ProtocolErrorCode.INVALID_PARAMS,
                    f"skill {entry.name!r} did not load in this session: the"
                    " configuration filters it out or it could not be read",
                )
        except BaseException:
            await self._restore_session_skill_pins(before)
            raise
        return SkillsSessionImportResponse(
            runtime=refreshed.runtime, status=refreshed.status, skill_name=loaded
        )

    async def _restore_session_skill_pins(
        self, pins: tuple[ManifestEntry, ...]
    ) -> None:
        """A refused or failed pick leaves the session on the picks it had."""
        await self._host.save_session_skill_pins(pins)
        try:
            await self._host.refresh()
        except Exception:
            logger.exception("Failed to reload the session's skills after a pick")

    async def _set_version(
        self, params: SkillsSetVersionParams
    ) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        try:
            await set_skill_version(
                self._host.config,
                params.name,
                params.version,
                self._scope(params.scope),
                self._host.skill_roots,
                credentials=self._host.credentials,
            )
        except RegistrySkillsError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, exc.reason) from exc
        return await self._refreshed()

    async def _set_latest(
        self, params: SkillsSetLatestParams
    ) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        try:
            await set_skill_latest(
                self._host.config,
                params.name,
                self._scope(params.scope),
                self._host.skill_roots,
                credentials=self._host.credentials,
            )
        except RegistrySkillsError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, exc.reason) from exc
        return await self._refreshed()

    async def _set_alias(self, params: SkillsSetAliasParams) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        try:
            await set_skill_alias(
                self._host.config,
                params.name,
                params.alias,
                self._scope(params.scope),
                self._host.skill_roots,
                credentials=self._host.credentials,
            )
        except RegistrySkillsError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, exc.reason) from exc
        return await self._refreshed()

    async def _remove(self, params: SkillsRemoveParams) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        try:
            await asyncio.to_thread(
                remove_skill,
                params.name,
                self._scope(params.scope),
                self._host.skill_roots,
            )
        except (RegistrySkillsError, OSError) as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        return await self._refreshed()

    async def _set_enabled(
        self, params: SkillsSetEnabledParams
    ) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        self._require_toggleable(params.name)
        await set_skill_enabled(
            self._host.config_orchestrator,
            params.name,
            params.enabled,
            reason="toggle a skill from the skills browser",
        )
        return await self._refreshed()

    async def _convert_local(
        self, params: SkillsConvertLocalParams
    ) -> SkillsConvertResponse:
        self._begin_mutation(params.session_id)
        try:
            target = await asyncio.to_thread(
                convert_skill_to_local,
                params.name,
                self._scope(params.scope),
                self._host.skill_roots,
            )
        except (RegistrySkillsError, OSError) as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        refreshed = await self._host.refresh()
        return SkillsConvertResponse(
            converted=target is not None,
            runtime=refreshed.runtime,
            status=refreshed.status,
        )

    async def _delete_local(
        self, params: SkillsDeleteLocalParams
    ) -> RuntimeMutationResponse:
        """Only a local skill file the session lists can be deleted, never a plugin's,
        a built-in or a registry copy, so ``path`` cannot reach anything else.
        """
        self._begin_mutation(params.session_id)
        if self._host.sandboxed:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                "skills of a sandboxed session are deleted inside the sandbox",
            )
        installed = self._host.installed_skills().skills
        row = next(
            (s for s in installed if s.source == "local" and s.path == params.path),
            None,
        )
        if row is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"no local skill at {params.path!r}"
            )
        folder = listed_skill_folder(Path(params.path), self._host.local_skill_dirs)
        if folder is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"no local skill at {params.path!r}"
            )
        try:
            await asyncio.to_thread(delete_skill_folder, folder / ENTRY_FILE)
        except OSError as exc:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR, f"Cannot delete {row.name!r}: {exc}"
            ) from exc
        return await self._refreshed()

    def _require_toggleable(self, name: str) -> None:
        """Refuse a name the browser itself would not offer a toggle for.

        Without this an SDK or extension client can misspell a skill and get a
        success back while the typo is written into the user's config, where
        only hand-editing removes it. The browser already enforces this; the
        server has to as well, since it is the shared entry point.
        """
        installed = self._host.installed_skills().skills
        row = next((s for s in installed if s.name == name), None)
        if row is None:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS, f"no installed skill named {name!r}"
            )
        if row.locked:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                f"{name!r} is fixed by configuration and cannot be toggled here",
            )

    def _begin_mutation(self, session_id: str) -> None:
        self._host.require_session(session_id)
        self._host.require_no_session_rewrite()

    async def _refreshed(self) -> RuntimeMutationResponse:
        return await self._host.refresh()

    def _scope(self, scope: str) -> SkillScope:
        if scope == "project":
            return SkillScope.PROJECT
        if scope == "global":
            return SkillScope.GLOBAL
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS, f"invalid skill scope: {scope!r}"
        )
