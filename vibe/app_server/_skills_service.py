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
import contextlib
from pathlib import Path
from typing import Any, Protocol

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
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
    SkillsDetailParams,
    SkillsDetailResponse,
    SkillsImportParams,
    SkillsInstalledParams,
    SkillsInstalledResponse,
    SkillsListParams,
    SkillsListResponse,
    SkillsRemoveParams,
    SkillsSetAliasParams,
    SkillsSetLatestParams,
    SkillsSetVersionParams,
    SkillsUpdatesParams,
    SkillsUpdatesResponse,
    SkillsVersionsParams,
    SkillsVersionsResponse,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.skills.models import SkillScope
from vibe.core.skills.registry import (
    RegistrySkillsError,
    check_new_versions,
    check_updates,
    convert_skill_to_local,
    get_skill_body,
    get_skill_details,
    has_registry_endpoint,
    import_skill,
    list_catalog,
    list_skill_versions,
    project_scope_available,
    publish_local_pins,
    remove_skill,
    set_skill_alias,
    set_skill_latest,
    set_skill_version,
)

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
    def skill_roots(self) -> list[Path]:
        """Project roots a scoped install may write into."""
        ...

    def require_idle(self) -> None:
        """Raise if a mutation would land mid-turn."""
        ...

    def require_session(self, session_id: str) -> None:
        """Raise if ``session_id`` is not this session."""
        ...

    def list_skills(self) -> list[SkillSummary]:
        """The catalogue behind ``/skill-name``, de-duped, project-wins."""
        ...

    def installed_skills(self) -> list[SkillSummary]:
        """Rows for the browser: one per name+scope, not de-duped."""
        ...

    async def refresh(self) -> RuntimeSnapshot:
        """Re-read the workspace after a mutation and return the new snapshot."""
        ...


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
            case _:
                raise method_not_found(method)
        return DispatchResult(response, runtime_updated=runtime_updated)

    def _list(self, params: SkillsListParams) -> SkillsListResponse:
        self._host.require_session(params.session_id)
        return SkillsListResponse(skills=self._host.list_skills())

    def _installed(self, params: SkillsInstalledParams) -> SkillsInstalledResponse:
        self._host.require_session(params.session_id)
        return SkillsInstalledResponse(skills=self._host.installed_skills())

    async def _catalog(self, params: SkillsCatalogParams) -> SkillsCatalogResponse:
        self._host.require_session(params.session_id)
        config = self._host.config
        roots = self._host.skill_roots
        project_available = project_scope_available(roots)
        authenticated = await has_registry_endpoint(config)
        if not authenticated:
            return SkillsCatalogResponse(
                skills=[],
                updates={},
                loaded=True,
                project_available=project_available,
                authenticated=False,
            )
        try:
            catalog = await list_catalog(config)
            updates = {
                u.name: u.latest_version for u in await check_updates(config, roots)
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
        versions = await list_skill_versions(self._host.config, params.skill_id)
        return SkillsVersionsResponse(
            versions=[
                SkillVersionView(version=v.version, aliases=list(v.aliases))
                for v in versions
            ]
        )

    async def _updates(self, params: SkillsUpdatesParams) -> SkillsUpdatesResponse:
        self._host.require_session(params.session_id)
        updates = await check_new_versions(self._host.config, self._host.skill_roots)
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
        detail = await get_skill_details(
            config, params.skill_id, version=params.version
        )
        if detail is not None:
            return SkillsDetailResponse(
                detail=SkillDetailView.model_validate(detail.model_dump())
            )
        try:
            body = await get_skill_body(config, params.skill_id, version=params.version)
        except RegistrySkillsError:
            body = None
        return SkillsDetailResponse(detail=None, body=body)

    async def _import(self, params: SkillsImportParams) -> RuntimeMutationResponse:
        self._begin_mutation(params.session_id)
        try:
            await import_skill(
                self._host.config,
                params.skill_id,
                version=params.version,
                alias=params.alias,
                scope=self._scope(params.scope),
                roots=self._host.skill_roots,
            )
        except RegistrySkillsError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, exc.reason) from exc
        return await self._refreshed()

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
        return SkillsConvertResponse(
            converted=target is not None, runtime=await self._host.refresh()
        )

    def _begin_mutation(self, session_id: str) -> None:
        self._host.require_idle()
        self._host.require_session(session_id)

    async def _refreshed(self) -> RuntimeMutationResponse:
        with contextlib.suppress(RegistrySkillsError, OSError):
            await publish_local_pins(self._host.skill_roots)
        return RuntimeMutationResponse(runtime=await self._host.refresh())

    def _scope(self, scope: str) -> SkillScope:
        if scope == "project":
            return SkillScope.PROJECT
        if scope == "global":
            return SkillScope.GLOBAL
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS, f"invalid skill scope: {scope!r}"
        )
