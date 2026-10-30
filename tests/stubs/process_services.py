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

from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._process_services import ProcessServices
from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult


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

    async def fetch(self, base_url: str, _api_key: str) -> ManagedConfigResult:
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


def fake_process_services(
    org_server: FakeOrgConfigServer | None = None, **org_config: Any
) -> ProcessServices:
    """``org_config`` goes to ``OrgConfigService`` (policy, first wait...)."""
    server = org_server or FakeOrgConfigServer()
    return ProcessServices(
        org_config=OrgConfigService(fetch=server.fetch, **org_config)
    )


__all__ = ["FakeOrgConfigServer", "fake_process_services"]
