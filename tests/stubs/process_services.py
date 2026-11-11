"""Process services for tests: fresh for each test, never on the network.

Tests take the ``process_services`` fixture (``tests/conftest.py``) and pass it
where production passes the entry point's ``ProcessServices``; ``org_server`` is
the managed-config endpoint those services fetch from.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._experiments_service import ExperimentsService
from vibe.app_server._mcp_discovery_service import MCPDiscoveryService
from vibe.app_server._model_catalog_service import ModelCatalogService
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._skill_shadows import ShadowWarnings
from vibe.app_server._skill_store_cleanup import SkillStoreCleanup
from vibe.app_server._worktree_housekeeping import WorktreeHousekeeping
from vibe.app_server.connector_catalog import SharedConnectorCatalog
from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response
from vibe.core.platform_auth import PlatformCredentialProvider
from vibe.setup.auth._oidc_tenant import OidcTenantResolver


@dataclass
class FakeOrgConfigServer:
    """The managed-config endpoint.

    Answers each fetch with the next of ``answers``, repeating the last: a TOML
    the org enforces, ``""`` for an org that enforces nothing, or ``None`` for
    a failed fetch (the default: offline). ``release``, when set, holds every
    fetch until it is set.
    """

    answers: list[str | None] = field(default_factory=lambda: [None])
    fetches: int = 0
    base_urls: list[str] = field(default_factory=list)
    release: asyncio.Event | None = None
    respond: Callable[[], Awaitable[ManagedConfigResult]] | None = None
    """Answers every fetch instead of ``answers``, when set."""

    def serve(self, *answers: str | None) -> None:
        self.answers = list(answers)

    async def fetch(
        self, base_url: str, _credentials: PlatformCredentialProvider
    ) -> ManagedConfigResult:
        self.base_urls.append(base_url)
        if self.respond is not None:
            self.fetches += 1
            return await self.respond()
        answer = self.answers[min(self.fetches, len(self.answers) - 1)]
        self.fetches += 1
        if self.release is not None:
            await self.release.wait()
        if answer is None:
            return ManagedConfigResult(error="managed config fetch is not mocked")
        if not answer:
            return ManagedConfigResult(config=ManagedConfig(state="disabled"))
        return ManagedConfigResult(config=ManagedConfig(state="enabled", toml=answer))


def runnable_catalog(*model_ids: str) -> ModelCatalogResponse:
    """A catalog of models the agent loop can run (function calling on)."""
    parsed = parse_model_catalog_response({
        "models": [
            {
                "id": model_id,
                "label": model_id,
                "capabilities": {"function_calling": True},
            }
            for model_id in model_ids
        ]
    })
    assert parsed is not None
    return parsed


@dataclass
class FakeModelCatalogServer:
    """The ``/model-catalog`` endpoint.

    Answers each fetch with the next of ``answers``, repeating the last;
    ``None`` is a failed fetch (the default: offline). ``release``, when set,
    holds every fetch until it is set.
    """

    answers: list[ModelCatalogResponse | None] = field(default_factory=lambda: [None])
    fetches: int = 0
    api_bases: list[str] = field(default_factory=list)
    release: asyncio.Event | None = None

    def serve(self, *answers: ModelCatalogResponse | None) -> None:
        self.answers = list(answers)

    async def fetch(
        self, api_base: str, _credentials: PlatformCredentialProvider
    ) -> ModelCatalogResponse | None:
        self.api_bases.append(api_base)
        answer = self.answers[min(self.fetches, len(self.answers) - 1)]
        self.fetches += 1
        if self.release is not None:
            await self.release.wait()
        return answer


def fake_process_services(
    org_server: FakeOrgConfigServer | None = None,
    *,
    connector_catalog: SharedConnectorCatalog | None = None,
    experiments: ExperimentsService | None = None,
    model_catalog_server: FakeModelCatalogServer | None = None,
    **org_config: Any,
) -> ProcessServices:
    """``org_config`` goes to ``OrgConfigService`` (policy, first wait...). The
    connector catalog fetches from the bootstrap endpoint unless given: tests
    mock it with ``respx``, and connectors are off by default. So does the
    experiments service: the shared test config keeps telemetry off, so a test
    about experiments turns it on and mocks the eval endpoint. The model
    catalog fetches from ``model_catalog_server``, offline unless given.
    """
    server = org_server or FakeOrgConfigServer()
    catalog_server = model_catalog_server or FakeModelCatalogServer()
    background = BackgroundWorker()
    return ProcessServices(
        org_config=OrgConfigService(fetch=server.fetch, **org_config),
        connector_catalog=connector_catalog or SharedConnectorCatalog(),
        oidc_tenants=OidcTenantResolver(),
        background=background,
        worktrees=WorktreeHousekeeping(background),
        experiments=experiments or ExperimentsService(),
        mcp_discovery=MCPDiscoveryService(),
        skill_shadows=ShadowWarnings(),
        skill_store=SkillStoreCleanup(background),
        open_sessions=OpenSessions(),
        # Looked up per fetch, so a test can patch the server's answers.
        model_catalog=ModelCatalogService(
            fetch=lambda api_base, credentials: catalog_server.fetch(
                api_base, credentials
            )
        ),
    )


__all__ = [
    "FakeModelCatalogServer",
    "FakeOrgConfigServer",
    "fake_process_services",
    "runnable_catalog",
]
