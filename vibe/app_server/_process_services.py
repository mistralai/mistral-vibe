"""The services one OS process shares across all its channels and sessions.

A multiplexed app server opens one channel per client connection, and each
channel builds its own ``HarnessProcess``. What must be shared by all of them
(a remote value refreshed once for everyone, say) lives here, built once by
the entry point and handed to every ``HarnessProcess``. Never in a module
global: tests then get fresh services each, and a service's lifetime is the
process's, visible at the one place that creates and closes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server._org_config_service import OrgConfigService


@dataclass(frozen=True, slots=True)
class ProcessServices:
    org_config: OrgConfigService

    @classmethod
    def create(cls) -> ProcessServices:
        """The production services; an entry point calls this once."""
        return cls(org_config=OrgConfigService())

    async def close(self) -> None:
        await self.org_config.close()


__all__ = ["ProcessServices"]
