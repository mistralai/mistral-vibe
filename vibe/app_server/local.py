from __future__ import annotations

from vibe.app_server._runtime import (
    ClientDescriptor,
    ContinueSessionIntent,
    HarnessProcess,
    LocalHarnessOptions,
    LocalSessionIntent,
    NewSessionIntent,
    ResumeSessionIntent,
    create_harness_server,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.host import AppServerHost
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair

__all__ = [
    "ClientDescriptor",
    "ContinueSessionIntent",
    "LocalHarness",
    "LocalHarnessHost",
    "LocalHarnessOptions",
    "LocalSessionIntent",
    "NewSessionIntent",
    "ResumeSessionIntent",
]


class LocalHarness:
    def __init__(self, options: LocalHarnessOptions) -> None:
        self._options = options
        self._started = False
        self._host = LocalHarnessHost()

    async def start(self) -> AppServerSession:
        if self._started:
            raise RuntimeError("The local app-server harness has already been started")
        self._started = True

        return await self._host.start(self._options)

    async def connect(self) -> AppServerHost:
        if self._started:
            raise RuntimeError("The local app-server harness has already been started")
        self._started = True
        return await self._host.connect(self._options)


class LocalHarnessHost:
    def __init__(self) -> None:
        self._process: HarnessProcess | None = None
        self._experimental_harness: bool | None = None

    async def start(self, options: LocalHarnessOptions) -> AppServerSession:
        host = await self.connect(options)
        try:
            return await host.open_session()
        except BaseException:
            await host.close()
            raise

    async def connect(self, options: LocalHarnessOptions) -> AppServerHost:
        process = self._process_for(options.experimental_harness)
        client_transport, server_transport = memory_transport_pair()
        harness = await create_harness_server(
            server_transport, transport_kind="in_process", process=process
        )
        client = AppServerClient(client_transport, run_peer=harness.serve)
        resume_session_id: str | None = None
        continue_session = False
        match options.session:
            case ResumeSessionIntent(session_id=session_id):
                resume_session_id = session_id
            case ContinueSessionIntent():
                continue_session = True
        return await AppServerHost.connect(
            client,
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
            resume_session_id=resume_session_id,
            continue_session=continue_session,
            client_tool_handler=options.client_tool_handler,
            client_factory=harness.connect_client,
        )

    async def close(self) -> None:
        if self._process is not None:
            await self._process.close()

    def _process_for(self, experimental_harness: bool) -> HarnessProcess:
        if self._process is None:
            self._process = HarnessProcess(experimental_harness=experimental_harness)
            self._experimental_harness = experimental_harness
            return self._process
        if self._experimental_harness != experimental_harness:
            raise RuntimeError(
                "A LocalHarnessHost cannot mix legacy and Unified Harness backends"
            )
        return self._process
