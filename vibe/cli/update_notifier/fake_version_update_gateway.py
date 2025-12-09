from __future__ import annotations

from vibe.cli.update_notifier.version_update_gateway import (
    VersionUpdate,
    VersionUpdateGateway,
    VersionUpdateGatewayError,
)


class FakeVersionUpdateGateway(VersionUpdateGateway):
    def __init__(
        self,
        update: VersionUpdate | None = None,
        error: VersionUpdateGatewayError | None = None,
    ) -> None:
        self._update: VersionUpdate | None = update
        self._error = error
        self.fetch_update_calls = 0

    async def fetch_update(self) -> VersionUpdate | None:
        self.fetch_update_calls += 1
        if self._error is not None:
            raise self._error
        return self._update
