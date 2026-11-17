"""The sessions every channel of this process has open.

A multiplexed app server opens each session on its own channel, with its own
session Host. Archiving the sessions a session started reaches sessions other
channels hold: archived from the store, the channel holding one would save its
own metadata over the archive. So each Host registers here, and the archive
goes through the Host that holds the session. A setting written without a
session reaches the open ones the same way: each re-reads its config files.
"""

from __future__ import annotations

from typing import Protocol

from vibe.app_server.protocol import SessionArchiveParams, SessionArchiveResponse


class OpenSessionHost(Protocol):
    async def archive_open(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse | None:
        """None when this Host does not hold the session open."""
        ...

    async def reload_config_files(self) -> None:
        """Have every open session re-read the config files."""
        ...


class OpenSessions:
    def __init__(self) -> None:
        self._hosts: list[OpenSessionHost] = []

    def register(self, host: OpenSessionHost) -> None:
        if host not in self._hosts:
            self._hosts.append(host)

    def unregister(self, host: OpenSessionHost) -> None:
        if host in self._hosts:
            self._hosts.remove(host)

    async def archive(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse | None:
        """Archived through the Host that holds the session open; None when
        none does.
        """
        for host in tuple(self._hosts):
            response = await host.archive_open(params)
            if response is not None:
                return response
        return None

    async def reload_config_files(self) -> None:
        """Every open session re-reads the config files, so a setting written
        without a session takes effect in the sessions already open.
        """
        for host in tuple(self._hosts):
            await host.reload_config_files()


__all__ = ["OpenSessionHost", "OpenSessions"]
