from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
from typing import cast
from uuid import UUID

import pytest

from mistralai_vibe_local_harness.vibe._storage import (
    StoredSession,
    UnifiedSessionStore,
)
from vibe.app_server._claude_import import ClaudeImportService
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._import_jobs import (
    ImportJobConflictError,
    ImportJobService,
    ImportJobSnapshot,
)
from vibe.app_server._imports import ImportsService
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._unified_import_writer import UnifiedSessionImportWriter
from vibe.app_server._vibe_projects import VibeProjectService
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import ImportHarness, ImportJobStatus
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    ImportDiscoveryResponse,
    ImportJobReadResponse,
    ImportJobStartResponse,
    ImportJobUpdatedParams,
    Notification,
    SessionOptions,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import SessionLoggingConfig
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.imports._harness_port import ImportDiscoverySummary, ImportProject
from vibe.core.imports._session import VibeSessionPayload
from vibe.core.imports.adapters.claude_project import ClaudeProjectSource
from vibe.core.imports.adapters.claude_session import (
    ClaudeSessionSource,
    ClaudeSessionSummary,
    ClaudeTranscript,
)


class BlockingImportRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class BlockingImportHarness:
    def __init__(self, runner: BlockingImportRunner) -> None:
        self._runner = runner

    def discover(self) -> ImportDiscoverySummary:
        return ImportDiscoverySummary(project_count=0, session_count=0)

    async def run(self) -> None:
        await self._runner.run()


class ThreadRecordingImportHarness:
    def __init__(self) -> None:
        self.discovery_thread_id: int | None = None

    def discover(self) -> ImportDiscoverySummary:
        self.discovery_thread_id = threading.get_ident()
        return ImportDiscoverySummary(project_count=1, session_count=2)

    async def run(self) -> None:
        pass


class ThreadRecordingClaudeSessionSource(ClaudeSessionSource):
    def __init__(self, projects_path: Path) -> None:
        super().__init__(projects_path)
        self.read_thread_id: int | None = None

    def read(self, summary: ClaudeSessionSummary) -> ClaudeTranscript:
        self.read_thread_id = threading.get_ident()
        return super().read(summary)


class RecordingProjectProvisioner:
    def __init__(self) -> None:
        self.projects: list[ImportProject] = []

    async def ensure_projects(
        self, projects: list[ImportProject]
    ) -> list[ImportProject]:
        self.projects.extend(projects)
        return projects


class RecordingProjectLinks:
    def __init__(
        self,
        failing_roots: set[str] | None = None,
        canonical_roots: dict[str, str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failing_roots = failing_roots or set()
        self.canonical_roots = canonical_roots or {}

    async def ensure_import_project(
        self, root_path: str, name: str
    ) -> dict[str, object]:
        self.calls.append((root_path, name))
        if root_path in self.failing_roots:
            raise RuntimeError("project provisioning failed")
        # ProjectLinksController._link_dict is the producer of this response shape.
        return {
            "link": {
                "projectId": "project-id",
                "projectName": name,
                "directoryPath": self.canonical_roots.get(root_path, root_path),
            }
        }


class CanonicalizingProjectProvisioner(RecordingProjectProvisioner):
    def __init__(self, canonical_cwd: Path) -> None:
        super().__init__()
        self.canonical_cwd = canonical_cwd

    async def ensure_projects(
        self, projects: list[ImportProject]
    ) -> list[ImportProject]:
        self.projects.extend(projects)
        return [
            replace(project, cwd=self.canonical_cwd, source_cwd=project.cwd)
            for project in projects
        ]


class RecordingSessionWriter:
    def __init__(self, failures: dict[str, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.written: list[VibeSessionPayload] = []

    async def write(self, session: VibeSessionPayload) -> None:
        failure = self.failures.get(session.source_session_id)
        if failure is not None:
            raise failure
        self.written.append(session)


@pytest.mark.asyncio
async def test_import_discovery_runs_outside_the_event_loop_thread() -> None:
    harness = ThreadRecordingImportHarness()
    imports = ImportsService({ImportHarness.CLAUDE: harness})

    await imports.discover()

    assert harness.discovery_thread_id is not None
    assert harness.discovery_thread_id != threading.get_ident()


@pytest.mark.asyncio
async def test_import_job_is_single_flight_and_reports_status_revisions() -> None:
    runner = BlockingImportRunner()
    service = ImportJobService()
    updates = []

    async def record_update(snapshot: ImportJobSnapshot) -> None:
        updates.append(snapshot)

    reserved = await service.reserve(runner, on_updated=record_update)
    assert reserved.status is ImportJobStatus.QUEUED

    with pytest.raises(ImportJobConflictError):
        await service.reserve(runner)

    service.start_reserved(reserved.id)
    await runner.started.wait()
    running = await service.read(after_event_id=0, wait_ms=0)
    assert running.job is not None
    assert running.job.id == reserved.id
    assert running.job.status is ImportJobStatus.RUNNING
    assert running.job.message is None

    runner.release.set()
    completed = await service.read(after_event_id=running.event_id, wait_ms=1_000)
    assert completed.job is not None
    assert completed.job.status is ImportJobStatus.COMPLETED
    assert [
        update.job.status if update.job is not None else None for update in updates
    ] == [ImportJobStatus.RUNNING, ImportJobStatus.COMPLETED]


@pytest.mark.asyncio
async def test_sessionless_import_job_sends_status_updates(
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A process-owned import service on a sessionless connection.
    *Do*: Start an import job and consume its status notifications.
    *Assert*: The connection receives running and completed updates.
    """
    # Prepare
    runner = BlockingImportRunner()
    imports = ImportsService({ImportHarness.CLAUDE: BlockingImportHarness(runner)})
    process = HarnessProcess(services=process_services)
    process.imports = imports
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    incoming = client.incoming()
    try:
        # Do
        await client.initialize(ClientInfo(name="import-test", version="1"))
        await client.notify("initialized")
        started = validate_wire(
            ImportJobStartResponse,
            await client.request("imports/start", {"harnesses": ["claude"]}),
        )
        running = await asyncio.wait_for(anext(incoming), timeout=1)
        assert isinstance(running, Notification)
        running_update = validate_wire(ImportJobUpdatedParams, running.params)

        runner.release.set()
        completed = await asyncio.wait_for(anext(incoming), timeout=1)
        assert isinstance(completed, Notification)
        completed_update = validate_wire(ImportJobUpdatedParams, completed.params)

        # Assert
        assert running.method == "imports/updated"
        assert running_update.job is not None
        assert running_update.job.id == started.job.id
        assert running_update.job.status is ImportJobStatus.RUNNING
        assert completed_update.job is not None
        assert completed_update.job.status is ImportJobStatus.COMPLETED
    finally:
        runner.release.set()
        await incoming.aclose()
        await client.close()
        await process.close()


@pytest.mark.asyncio
async def test_session_stop_does_not_cancel_process_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A process-owned import job running beside an attached session.
    *Do*: Stop the session, then allow the import to finish.
    *Assert*: Session teardown does not cancel the import job.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    runner = BlockingImportRunner()
    imports = ImportsService({ImportHarness.CLAUDE: BlockingImportHarness(runner)})
    completed = asyncio.Event()

    async def record_update(_method: str, params: object) -> None:
        if (
            isinstance(params, ImportJobUpdatedParams)
            and params.job is not None
            and params.job.status is ImportJobStatus.COMPLETED
        ):
            completed.set()

    process = HarnessProcess(services=process_services)
    process.imports = imports
    try:
        client_transport, server_transport = memory_transport_pair()
        harness = await create_harness_server(
            server_transport, transport_kind="in_process", process=process
        )
        session = await AppServerSession.start(
            AppServerClient(client_transport, run_peer=harness.serve),
            client_info=ClientInfo(name="import-test", version="1"),
            capabilities=ClientCapabilities(),
            session_options=SessionOptions(cwd=str(tmp_path)),
        )

        # Do
        start = await imports.start([ImportHarness.CLAUDE], notify=record_update)
        assert start.after_response is not None
        start.after_response()
        await runner.started.wait()
        await session.close()
        cancelled_after_session_stop = runner.cancelled.is_set()
        runner.release.set()
        await completed.wait()
        snapshot = validate_wire(
            ImportJobReadResponse,
            (await imports.read(after_event_id=0, wait_ms=0)).response,
        )

        # Assert
        assert not cancelled_after_session_stop
        assert snapshot.job is not None
        assert snapshot.job.status is ImportJobStatus.COMPLETED
    finally:
        runner.release.set()
        await process.close()


@pytest.mark.asyncio
async def test_claude_runner_imports_every_session_from_existing_projects(
    tmp_path: Path,
) -> None:
    """*Prepare*: Source and nested sessions under a project with a canonical link.
    *Do*: Run the Claude importer.
    *Assert*: Sessions target the canonical root and retain their source cwd.
    """
    # Prepare
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    canonical_workspace = tmp_path / "canonical-workspace"
    canonical_workspace.mkdir()
    nested_workspace = workspace / "vibe"
    nested_workspace.mkdir()
    missing_workspace = tmp_path / "missing-workspace"
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps({"projects": {str(workspace): {}, str(missing_workspace): {}}}),
        encoding="utf-8",
    )
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    nested_session_id = UUID("33333333-3333-3333-3333-333333333333")
    missing_session_id = UUID("22222222-2222-2222-2222-222222222222")
    projects_path = tmp_path / ".claude" / "projects" / "workspace"
    projects_path.mkdir(parents=True)
    transcript_path = projects_path / f"{session_id}.jsonl"
    transcript_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "type": "user",
                    "cwd": str(workspace),
                    "message": {"role": "user", "content": "inspect"},
                },
                {
                    "type": "assistant",
                    "cwd": str(workspace),
                    "message": {"role": "assistant", "content": "done"},
                },
            ]
        )
        + "\n"
    )
    nested_transcript_path = projects_path / f"{nested_session_id}.jsonl"
    nested_transcript_path.write_text(
        json.dumps({
            "type": "user",
            "cwd": str(nested_workspace),
            "message": {"role": "user", "content": "inspect nested"},
        })
        + "\n",
        encoding="utf-8",
    )
    missing_transcript_path = projects_path / f"{missing_session_id}.jsonl"
    missing_transcript_path.write_text(
        json.dumps({
            "type": "user",
            "cwd": str(missing_workspace),
            "message": {"role": "user", "content": "inspect"},
        })
        + "\n",
        encoding="utf-8",
    )

    async def build_writer() -> UnifiedSessionImportWriter:
        return UnifiedSessionImportWriter(
            SessionLoggingConfig(enabled=True, save_dir=str(tmp_path / "sessions"))
        )

    provisioner = CanonicalizingProjectProvisioner(canonical_workspace)
    session_source = ThreadRecordingClaudeSessionSource(projects_path.parent)
    runner = ClaudeImportService(
        ClaudeProjectSource(config_path), session_source, provisioner, build_writer
    )

    # Do
    await runner.run()

    # Assert
    assert session_source.read_thread_id is not None
    assert session_source.read_thread_id != threading.get_ident()

    session_root = tmp_path / "sessions" / "unified"
    session_ids = sorted(path.name for path in session_root.iterdir() if path.is_dir())
    assert len(session_ids) == 2
    assert provisioner.projects == [ImportProject(name="workspace", cwd=workspace)]
    loaded_sessions = [
        UnifiedSessionStore(tmp_path / "sessions", session_id).load()
        for session_id in session_ids
    ]
    assert {
        session.runtime_state.session_metadata.cwd for session in loaded_sessions
    } == {str(canonical_workspace)}

    def imported_from(session: StoredSession, source_session_id: str) -> bool:
        external_import = session.runtime_state.session_metadata.external_import
        return (
            external_import is not None
            and external_import.session_id == source_session_id
        )

    imported_workspace_session = next(
        session
        for session in loaded_sessions
        if imported_from(session, str(session_id))
    )
    assert imported_workspace_session.interop_export is not None
    assert len(imported_workspace_session.interop_export.history) == 2
    imported_nested_session = next(
        session
        for session in loaded_sessions
        if imported_from(session, str(nested_session_id))
    )
    assert imported_nested_session.runtime_state.session_metadata.cwd == str(
        canonical_workspace
    )
    nested_import = (
        imported_nested_session.runtime_state.session_metadata.external_import
    )
    assert nested_import is not None
    assert nested_import.source_cwd == str(nested_workspace)


@pytest.mark.asyncio
async def test_claude_runner_skips_session_validation_failures(tmp_path: Path) -> None:
    """*Prepare*: Two valid transcripts and a writer rejecting the first.
    *Do*: Run the Claude importer.
    *Assert*: The validation failure is isolated and the second session is written.
    """
    # Prepare
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps({"projects": {str(workspace): {}}}), encoding="utf-8"
    )
    rejected_id = UUID("11111111-1111-1111-1111-111111111111")
    accepted_id = UUID("22222222-2222-2222-2222-222222222222")
    projects_path = tmp_path / ".claude" / "projects" / "workspace"
    projects_path.mkdir(parents=True)
    for session_id in (rejected_id, accepted_id):
        (projects_path / f"{session_id}.jsonl").write_text(
            json.dumps({
                "type": "user",
                "cwd": str(workspace),
                "message": {"role": "user", "content": "inspect"},
            })
            + "\n",
            encoding="utf-8",
        )
    writer = RecordingSessionWriter({str(rejected_id): ValueError("too large")})

    async def build_writer() -> RecordingSessionWriter:
        return writer

    runner = ClaudeImportService(
        ClaudeProjectSource(config_path),
        ClaudeSessionSource(projects_path.parent),
        RecordingProjectProvisioner(),
        build_writer,
    )

    # Do
    await runner.run()

    # Assert
    assert [session.source_session_id for session in writer.written] == [
        str(accepted_id)
    ]


@pytest.mark.asyncio
async def test_claude_runner_propagates_storage_failures(tmp_path: Path) -> None:
    """*Prepare*: A valid transcript and a writer failing with an OS error.
    *Do*: Run the Claude importer.
    *Assert*: The systemic storage failure aborts the import job.
    """
    # Prepare
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps({"projects": {str(workspace): {}}}), encoding="utf-8"
    )
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    projects_path = tmp_path / ".claude" / "projects" / "workspace"
    projects_path.mkdir(parents=True)
    (projects_path / f"{session_id}.jsonl").write_text(
        json.dumps({
            "type": "user",
            "cwd": str(workspace),
            "message": {"role": "user", "content": "inspect"},
        })
        + "\n",
        encoding="utf-8",
    )
    writer = RecordingSessionWriter({str(session_id): OSError("disk full")})

    async def build_writer() -> RecordingSessionWriter:
        return writer

    runner = ClaudeImportService(
        ClaudeProjectSource(config_path),
        ClaudeSessionSource(projects_path.parent),
        RecordingProjectProvisioner(),
        build_writer,
    )

    # Do
    with pytest.raises(OSError, match="disk full"):
        await runner.run()

    # Assert
    assert writer.written == []


@pytest.mark.asyncio
async def test_claude_runner_skips_an_invalid_transcript_and_continues(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps({"projects": {str(workspace): {}}}), encoding="utf-8"
    )
    invalid_session_id = UUID("11111111-1111-1111-1111-111111111111")
    valid_session_id = UUID("22222222-2222-2222-2222-222222222222")
    projects_path = tmp_path / ".claude" / "projects" / "workspace"
    projects_path.mkdir(parents=True)
    (projects_path / f"{invalid_session_id}.jsonl").write_text(
        "not-json\n", encoding="utf-8"
    )
    (projects_path / f"{valid_session_id}.jsonl").write_text(
        json.dumps({
            "type": "user",
            "cwd": str(workspace),
            "message": {"role": "user", "content": "inspect"},
        })
        + "\n",
        encoding="utf-8",
    )

    async def build_writer() -> UnifiedSessionImportWriter:
        return UnifiedSessionImportWriter(
            SessionLoggingConfig(enabled=True, save_dir=str(tmp_path / "sessions"))
        )

    runner = ClaudeImportService(
        ClaudeProjectSource(config_path),
        ClaudeSessionSource(projects_path.parent),
        RecordingProjectProvisioner(),
        build_writer,
    )
    await runner.run()

    session_root = tmp_path / "sessions" / "unified"
    stores = [
        UnifiedSessionStore(tmp_path / "sessions", path.name).load()
        for path in session_root.iterdir()
        if path.is_dir()
    ]
    assert len(stores) == 1
    external_import = stores[0].runtime_state.session_metadata.external_import
    assert external_import is not None
    assert external_import.session_id == str(valid_session_id)


@pytest.mark.asyncio
async def test_app_server_discovers_claude_projects_and_their_sessions(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_workspace = tmp_path / "missing-workspace"
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps({"projects": {str(workspace): {}, str(missing_workspace): {}}}),
        encoding="utf-8",
    )
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    projects_path = tmp_path / ".claude" / "projects" / "workspace"
    projects_path.mkdir(parents=True)
    transcript_path = projects_path / f"{session_id}.jsonl"
    transcript_path.write_text(
        json.dumps({"cwd": str(workspace)}) + "\n", encoding="utf-8"
    )

    async def build_writer() -> UnifiedSessionImportWriter:
        return UnifiedSessionImportWriter(
            SessionLoggingConfig(enabled=True, save_dir=str(tmp_path / "sessions"))
        )

    _, transport = memory_transport_pair()
    server = AppServer(
        transport,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
        imports_service=ImportsService({
            ImportHarness.CLAUDE: ClaudeImportService(
                ClaudeProjectSource(config_path),
                ClaudeSessionSource(projects_path.parent),
                RecordingProjectProvisioner(),
                build_writer,
            )
        }),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
        ),
    )

    dispatched = await server._dispatch_request("imports/discover", {})

    response = cast(ImportDiscoveryResponse, dispatched.response)
    assert response.model_dump(mode="json", by_alias=True) == {
        "harnesses": [{"harness": "claude", "projectCount": 1, "sessionCount": 1}]
    }


@pytest.mark.asyncio
async def test_vibe_project_service_provisions_every_import_project() -> None:
    """*Prepare*: Source projects whose links resolve to canonical roots.
    *Do*: Provision the import projects.
    *Assert*: Returned projects retain source roots and use canonical destinations.
    """
    # Prepare
    project_links = RecordingProjectLinks(
        canonical_roots={"/workspace/one": "/repos/one", "/workspace/two": "/repos/two"}
    )
    service = VibeProjectService(project_links)
    projects = [
        ImportProject(name="One", cwd=Path("/workspace/one")),
        ImportProject(name="Two", cwd=Path("/workspace/two")),
    ]

    # Do
    provisioned = await service.ensure_projects(projects)

    # Assert
    assert project_links.calls == [("/workspace/one", "One"), ("/workspace/two", "Two")]
    assert provisioned == [
        ImportProject(
            name="One", cwd=Path("/repos/one"), source_cwd=Path("/workspace/one")
        ),
        ImportProject(
            name="Two", cwd=Path("/repos/two"), source_cwd=Path("/workspace/two")
        ),
    ]


@pytest.mark.asyncio
async def test_vibe_project_service_skips_failed_projects_and_continues() -> None:
    """*Prepare*: Two source projects with one provisioning failure.
    *Do*: Provision both projects.
    *Assert*: The failure is skipped and the successful project is canonicalized.
    """
    # Prepare
    project_links = RecordingProjectLinks({"/workspace/one"})
    service = VibeProjectService(project_links)
    projects = [
        ImportProject(name="One", cwd=Path("/workspace/one")),
        ImportProject(name="Two", cwd=Path("/workspace/two")),
    ]

    # Do
    provisioned = await service.ensure_projects(projects)

    # Assert
    assert project_links.calls == [("/workspace/one", "One"), ("/workspace/two", "Two")]
    assert provisioned == [
        ImportProject(
            name="Two", cwd=Path("/workspace/two"), source_cwd=Path("/workspace/two")
        )
    ]
