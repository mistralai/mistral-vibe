from __future__ import annotations

from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGateway,
    UpdateGatewayError,
)


class FakeUpdateGateway(UpdateGateway):
    def __init__(
        self, update: Update | None = None, error: UpdateGatewayError | None = None
    ) -> None:
        self._update: Update | None = update
        self._error = error
        self.fetch_update_calls = 0

    async def fetch_update(self) -> Update | None:
        self.fetch_update_calls += 1
        if self._error is not None:
            raise self._error
        return self._update
