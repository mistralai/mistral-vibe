"""A multiplexed channel that serves one run of ``vibe run-server``.

The channel's ``open`` options name the run's agent socket, its user config and
its API keys. The run gets its own agent socket mount, so an abort or a failed
handshake fails it alone, and only user-level files load: the project lives in
the sandbox the socket must serve.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from vibe.app_server._agent_socket import AgentSocketMount
from vibe.app_server._process_settings import pin_process_settings
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.multiplex import ChannelOpen
from vibe.app_server.transport import JsonRpcTransport
from vibe.core.config.harness_files import HarnessFilesManager


class RunChannelServer:
    def __init__(
        self,
        channel: JsonRpcTransport,
        options: ChannelOpen,
        *,
        additional_builtin_plugin_roots: Sequence[Path] = (),
    ) -> None:
        self._channel = channel
        self._options = options
        self._additional_builtin_plugin_roots = additional_builtin_plugin_roots
        self.mount: AgentSocketMount | None = None

    async def serve(self) -> None:
        settings = await pin_process_settings()
        # A failed handshake is latched on the mount and fails the session
        # open, which reports the run's outcome; it never fails the channel.
        mount = await AgentSocketMount.connect(
            Path(self._options.agent_socket).expanduser()
        )
        self.mount = mount
        try:
            mount.require_sandbox("vibe run-server runs every session in one")
            process = HarnessProcess(
                HarnessFilesManager(
                    sources=("user",), user_config=self._options.config
                ),
                additional_builtin_plugin_roots=self._additional_builtin_plugin_roots,
                agent_socket=mount,
                credentials=self._options.credentials,
                process_settings=settings,
            )
            server = await create_harness_server(
                self._channel, transport_kind="stdio", process=process
            )
            await server.serve()
        finally:
            mount.close()


__all__ = ["RunChannelServer"]
