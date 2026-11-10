"""Tests for the session artifact service (vibe.app_server._session_artifacts)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import UUID

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.app_server import FakeSessionBackendServices
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from tests.stubs.process_services import fake_process_services
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._plugin_mcp import PluginMCPCatalog
from vibe.app_server._session_artifacts import (
    ArtifactRequestHandler,
    SessionArtifactService,
    to_public_artifact,
)
from vibe.app_server._session_backend_port import (
    ResolvedMCPCatalog,
    SessionBackendError,
)
from vibe.app_server.events import (
    ArtifactCreated,
    ArtifactUpdated,
    ClientProjection,
    _parse_event_params,
)
from vibe.app_server.models import IdleSessionStatus, PublicSession, PublicSessionState
from vibe.app_server.protocol import (
    ArtifactCreatedParams,
    ArtifactsListParams,
    ArtifactsListResponse,
    ArtifactsMarkViewedParams,
    ArtifactsSyncParams,
    ArtifactUpdatedParams,
    EmptyResponse,
    Notification,
    ProtocolErrorCode,
    PublicGitHubPullRequestDetails,
    PublicGitLabMergeRequestDetails,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.git.forge import DiscoveredPullRequest, ForgeError
from vibe.core.session.artifacts import (
    ChecksSummary,
    GitHubPullRequestArtifact,
    GitHubPullRequestDetails,
    GitLabMergeRequestArtifact,
    GitLabMergeRequestDetails,
    SessionArtifact,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.session import SessionTelemetry
from vibe.core.tools.mcp.registry import MCPRegistry
from vibe.core.trusted_folders import TrustedFoldersManager
from vibe.core.types import SessionMetadata

SESSION_ID = "01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a5b"

GH_COMMAND = "gh pr create --title 'Fix login' --body 'details'"
GH_STDOUT = "https://github.com/mistralai/vibe/pull/123\n"
GLAB_COMMAND = "glab mr create -t 'Add feature'"
GLAB_STDOUT = "https://gitlab.com/team/project/-/merge_requests/7\n"


def _write_metadata(
    session_dir: Path, *, artifacts: list | None = None, session_id: str = SESSION_ID
) -> SessionMetadata:
    metadata = SessionMetadata(
        session_id=session_id,
        start_time="2026-10-13T10:00:00+00:00",
        end_time=None,
        git_commit=None,
        git_branch=None,
        environment={},
        username="tester",
        artifacts=artifacts or [],
    )
    (session_dir / "meta.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    return metadata


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "sessions" / SESSION_ID
    session_dir.mkdir(parents=True)
    _write_metadata(session_dir)
    return session_dir


@pytest.fixture
def service(session_dir: Path) -> SessionArtifactService:
    return SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)


class _GitHub:
    """GitHub as the pull request cache reads it: every PR answers
    ``snapshot``, or fails when it is ``None`` (as when the CLI is missing).

    Every check answers a new fingerprint, so each read is a full read.
    """

    def __init__(self) -> None:
        from vibe.core.git.forge_sync import PullRequestSnapshot

        self.snapshot: PullRequestSnapshot | None = None
        self.reads = 0
        self._fingerprints = iter(range(1_000_000))

    def checks(self, gh_path, host, refs):
        return {ref: f"fingerprint-{next(self._fingerprints)}" for ref in refs}, None

    def full(self, gh_path, host, refs, checked_at):
        from vibe.core.git.forge_sync import ForgeSyncError

        self.reads += len(refs)
        snapshot = self.snapshot
        return {
            ref: ForgeSyncError("stubbed: no provider read")
            if snapshot is None
            else snapshot
            for ref in refs
        }, None


@pytest.fixture(autouse=True)
def github(monkeypatch: pytest.MonkeyPatch) -> _GitHub:
    """Keep real provider reads out of the tests: every new artifact is read
    from its provider, and the default answer is a failed read (the artifact
    stands with its partial data). Tests that need a status set
    ``github.snapshot``.
    """
    from vibe.app_server import _pull_request_cache as cache_module

    fake = _GitHub()
    monkeypatch.setattr(cache_module, "read_github_checks", fake.checks)
    monkeypatch.setattr(cache_module, "read_github_pull_requests", fake.full)
    monkeypatch.setattr(cache_module, "find_cli_binary", lambda name: name)
    return fake


class TestCreatesArtifact:
    @pytest.mark.asyncio
    async def test_the_title_comes_from_the_provider(
        self, service: SessionArtifactService, github: _GitHub
    ):
        # The command passes the title through a heredoc: only the provider
        # knows it.
        github.snapshot = _snapshot(title="feat: heredoc title")
        command = "gh pr create --title \"$(cat <<'EOF'\nfeat: heredoc title\nEOF\n)\""
        [artifact] = await service.on_shell_effect(command, GH_STDOUT, "", "completed")
        assert to_public_artifact(artifact).display_title == "feat: heredoc title"

    @pytest.mark.asyncio
    async def test_github_pr(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        assert isinstance(artifact, GitHubPullRequestArtifact)
        assert artifact.provider == "github"
        assert artifact.session_id == SESSION_ID
        assert artifact.external_key == "github.com/mistralai/vibe/pull/123"
        assert artifact.external_url == "https://github.com/mistralai/vibe/pull/123"
        # The provider read fails in these tests: no title until it succeeds.
        assert artifact.display_title is None
        assert artifact.viewed_at is None
        assert artifact.details is not None
        assert artifact.details.repo_host == "github.com"
        assert artifact.details.repo_owner == "mistralai"
        assert artifact.details.repo_name == "vibe"
        assert artifact.details.pull_request_number == 123
        assert artifact.details.state == "open"

    @pytest.mark.asyncio
    async def test_loop_creating_two_prs_records_both(
        self, service: SessionArtifactService
    ):
        artifacts = await service.on_shell_effect(
            "for b in a b; do gh pr create --head $b; done",
            "https://github.com/mistralai/vibe/pull/1\n"
            "https://github.com/mistralai/vibe/pull/2\n",
            "",
            "completed",
        )
        assert [a.external_key for a in artifacts] == [
            "github.com/mistralai/vibe/pull/1",
            "github.com/mistralai/vibe/pull/2",
        ]
        assert len(service.list_artifacts()) == 2

    @pytest.mark.asyncio
    async def test_listed_prs_before_a_creation_are_not_recorded(
        self, service: SessionArtifactService
    ):
        artifacts = await service.on_shell_effect(
            "gh pr list --json url --jq '.[].url' && gh pr create --fill",
            "https://github.com/mistralai/vibe/pull/1\n"
            "https://github.com/mistralai/vibe/pull/2\n"
            "https://github.com/mistralai/vibe/pull/3\n",
            "",
            "completed",
        )
        assert [a.external_key for a in artifacts] == [
            "github.com/mistralai/vibe/pull/3"
        ]

    @pytest.mark.asyncio
    async def test_github_pr_draft(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            "gh pr create --draft", GH_STDOUT, "", "completed"
        )
        assert artifact.details is not None
        assert artifact.details.state == "draft"

    @pytest.mark.asyncio
    async def test_github_enterprise_host(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND,
            "https://github.mycompany.com/org/repo/pull/9\n",
            "",
            "completed",
        )
        assert artifact.external_key == "github.mycompany.com/org/repo/pull/9"
        assert artifact.details is not None
        assert artifact.details.repo_host == "github.mycompany.com"

    @pytest.mark.asyncio
    async def test_gitlab_mr_with_repo_id(
        self, service: SessionArtifactService, monkeypatch
    ):
        """repo_id resolved via glab api → external key uses the numeric id."""
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=4242),
        )
        [artifact] = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        assert isinstance(artifact, GitLabMergeRequestArtifact)
        assert artifact.provider == "gitlab"
        assert artifact.external_key == "gitlab.com/4242/merge_requests/7"
        assert artifact.display_title is None
        assert artifact.details is not None
        assert artifact.details.repo_host == "gitlab.com"
        assert artifact.details.repo_id == 4242
        assert artifact.details.project_path == "team/project"
        assert artifact.details.merge_request_iid == 7
        assert artifact.details.state == "open"

    @pytest.mark.asyncio
    async def test_gitlab_mr_repo_id_fallback(
        self, service: SessionArtifactService, monkeypatch
    ):
        """glab unavailable → external key falls back to project_path."""
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=None),
        )
        [artifact] = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        assert isinstance(artifact, GitLabMergeRequestArtifact)
        assert artifact.external_key == "gitlab.com/team/project/-/merge_requests/7"
        assert artifact.details is not None
        assert artifact.details.repo_id is None

    @pytest.mark.asyncio
    async def test_gitlab_dash_d_is_description_not_draft(
        self, service: SessionArtifactService, monkeypatch
    ):
        """glab -d means --description; the MR is not a draft."""
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=None),
        )
        [artifact] = await service.on_shell_effect(
            'glab mr create -d "my description"', GLAB_STDOUT, "", "completed"
        )
        assert artifact.details is not None
        assert artifact.details.state == "open"

    @pytest.mark.asyncio
    async def test_github_enterprise_uppercase_host_lowercased(
        self, service: SessionArtifactService
    ):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, "https://GitHub.COM/Org/Repo/pull/5\n", "", "completed"
        )
        assert artifact.external_key == "github.com/org/repo/pull/5"

    @pytest.mark.asyncio
    async def test_artifact_id_is_uuid(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        assert UUID(artifact.id).version == 5

    @pytest.mark.asyncio
    async def test_two_services_give_the_same_pr_the_same_id(self, session_dir: Path):
        first = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        second = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)

        [a] = await first.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        [b] = await second.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        assert a.id == b.id

    @pytest.mark.asyncio
    async def test_another_session_gets_another_id(self, tmp_path: Path):
        ids = []
        for session_id in (SESSION_ID, "01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a5c"):
            session_dir = tmp_path / session_id
            session_dir.mkdir()
            service = SessionArtifactService(
                session_id=session_id, session_dir=session_dir
            )
            [artifact] = await service.on_shell_effect(
                GH_COMMAND, GH_STDOUT, "", "completed"
            )
            ids.append(artifact.id)

        assert ids[0] != ids[1]

    @pytest.mark.asyncio
    async def test_url_in_stderr(self, service: SessionArtifactService):
        """gh prints the URL on stderr in some flows."""
        [artifact] = await service.on_shell_effect(
            GH_COMMAND,
            "",
            "remote: View the PR at https://github.com/org/repo/pull/3\n",
            "completed",
        )
        assert artifact.external_key == "github.com/org/repo/pull/3"

    @pytest.mark.asyncio
    async def test_no_title_in_command(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            "gh pr create --fill", GH_STDOUT, "", "completed"
        )
        assert artifact.display_title is None


class TestIgnoresNonArtifacts:
    @pytest.mark.asyncio
    async def test_non_pr_command(self, service: SessionArtifactService):
        assert (
            await service.on_shell_effect(
                "git push origin main", GH_STDOUT, "", "completed"
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_pr_list_command(self, service: SessionArtifactService):
        assert (
            await service.on_shell_effect("gh pr list", GH_STDOUT, "", "completed")
            == []
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["running", "pending", "cancelled", "skipped"])
    async def test_non_terminal_status(
        self, service: SessionArtifactService, status: str
    ):
        assert await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", status) == []

    @pytest.mark.asyncio
    async def test_failed_status_still_detected(self, service: SessionArtifactService):
        """A non-zero exit can still carry the URL (e.g. wrapper post-hook failed)."""
        [artifact] = await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "failed")

    @pytest.mark.asyncio
    async def test_no_url_in_output(self, service: SessionArtifactService):
        assert (
            await service.on_shell_effect(
                GH_COMMAND, "Creating pull request...", "", "completed"
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_empty_command(self, service: SessionArtifactService):
        assert await service.on_shell_effect("", GH_STDOUT, "", "completed") == []


class TestDedup:
    @pytest.mark.asyncio
    async def test_duplicate_pr_not_recreated(self, service: SessionArtifactService):
        [first] = await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        second = await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        assert second == []
        assert len(service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_pr_stored_under_another_key_form_not_recreated(
        self, service: SessionArtifactService
    ):
        [first] = await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        legacy = first.model_copy(
            update={"external_key": "github.com/MistralAI/vibe/pull/123"}
        )
        service._artifacts = {legacy.external_key: legacy}

        second = await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        assert second == []
        assert [a.id for a in service.list_artifacts()] == [first.id]

    @pytest.mark.asyncio
    async def test_same_pr_different_title_not_recreated(
        self, service: SessionArtifactService
    ):
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        second = await service.on_shell_effect(
            "gh pr create --title 'Other'", GH_STDOUT, "", "completed"
        )
        assert second == []

    @pytest.mark.asyncio
    async def test_two_different_prs_both_recorded(
        self, service: SessionArtifactService
    ):
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        [_] = await service.on_shell_effect(
            GH_COMMAND, "https://github.com/mistralai/vibe/pull/124\n", "", "completed"
        )
        assert len(service.list_artifacts()) == 2

    @pytest.mark.asyncio
    async def test_gitlab_path_then_repo_id_key_is_same_mr(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        """A later glab api success must not store the same MR twice."""
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(side_effect=[None, 4242]),
        )
        [first] = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        second = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        assert first.external_key == "gitlab.com/team/project/-/merge_requests/7"
        assert second == []
        assert len(service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_gitlab_repo_id_then_path_key_is_same_mr(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        """A later unresolved glab api must not store the same MR twice."""
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(side_effect=[4242, None]),
        )
        [first] = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        second = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        assert first.external_key == "gitlab.com/4242/merge_requests/7"
        assert second == []
        assert len(service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_enriched_new_artifact_is_not_its_own_duplicate(
        self, service: SessionArtifactService, github: _GitHub
    ):
        """Enrich-from-birth must not defeat the dedup check.

        The birth enrichment applies the provider snapshot before the dedup
        check runs; applying it used to insert the artifact as a side
        effect, so the check found the artifact itself and skipped the
        recording — no persist, no created notification.
        """
        github.snapshot = _snapshot()
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        # The enrichment fetch succeeded, so the snapshot really applies.
        details = to_public_artifact(artifact).details
        assert details is not None
        assert details.source_branch == "fix/login"
        assert len(service.list_artifacts()) == 1
        # A genuine duplicate is still deduplicated.
        second = await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        assert second == []
        assert len(service.list_artifacts()) == 1


class TestLoadAndList:
    @pytest.mark.asyncio
    async def test_load_from_metadata(self, session_dir: Path):
        [existing] = await SessionArtifactService(
            session_id=SESSION_ID, session_dir=session_dir
        ).on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        # Simulate a resume: fresh service seeded from the persisted metadata.
        metadata = SessionLoader.load_metadata(session_dir)
        resumed = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        resumed.load(metadata)

        artifacts = resumed.list_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0].external_key == existing.external_key
        assert artifacts[0].id == existing.id

    @pytest.mark.asyncio
    async def test_loaded_artifact_dedupes_new_detection(self, session_dir: Path):
        """A resumed session must not re-create an artifact it already recorded."""
        first = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        await first.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        resumed = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        resumed.load(SessionLoader.load_metadata(session_dir))

        assert (
            await resumed.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed") == []
        )
        assert len(resumed.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_list_artifacts_sorted_by_discovered_at(
        self, service: SessionArtifactService
    ):
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        await service.on_shell_effect(
            GH_COMMAND, "https://github.com/mistralai/vibe/pull/124\n", "", "completed"
        )
        artifacts = service.list_artifacts()
        assert len(artifacts) == 2
        assert artifacts[0].discovered_at <= artifacts[1].discovered_at


class TestPersistence:
    @pytest.mark.asyncio
    async def test_persistence_roundtrip(
        self, service: SessionArtifactService, session_dir: Path
    ):
        """Artifacts land in meta.json and re-parse through SessionMetadata."""
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )

        metadata = SessionLoader.load_metadata(session_dir)
        assert len(metadata.artifacts) == 1
        restored = metadata.artifacts[0]
        assert isinstance(restored, GitHubPullRequestArtifact)
        assert restored.id == artifact.id
        assert restored.external_key == "github.com/mistralai/vibe/pull/123"
        assert restored.details is not None
        assert restored.details.pull_request_number == 123

    @pytest.mark.asyncio
    async def test_persistence_preserves_other_metadata_fields(
        self, service: SessionArtifactService, session_dir: Path
    ):
        """Read-modify-write must not clobber unrelated meta.json fields."""
        metadata_path = session_dir / "meta.json"
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        raw["title"] = "My session"
        metadata_path.write_text(json.dumps(raw), encoding="utf-8")

        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        after = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert after["title"] == "My session"
        assert after["session_id"] == SESSION_ID
        assert len(after["artifacts"]) == 1

    @pytest.mark.asyncio
    async def test_corrupted_meta_json_does_not_raise(self, session_dir: Path):
        """B9: a corrupted meta.json must not break artifact creation."""
        (session_dir / "meta.json").write_text("not json {{{", encoding="utf-8")
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)

        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        # In-memory copy survives even though persistence failed.
        assert len(service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_missing_meta_json_skips_persist(self, tmp_path: Path):
        """Session not yet persisted → artifact kept in memory, no raise."""
        empty_dir = tmp_path / "empty-session"
        empty_dir.mkdir()
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=empty_dir)

        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        assert len(service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_persist_serializes_on_shared_lock(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """With the adapter's metadata lock shared, the read-modify-write of
        meta.json runs under it — serialized against the adapter's whole-file
        metadata writes (no lost updates in either direction).
        """
        import asyncio

        import vibe.app_server._session_artifacts as artifacts_module

        lock = asyncio.Lock()
        lock_held: list[bool] = []
        real_persist = artifacts_module.persist_metadata

        async def spy_persist(raw: dict[str, Any], target: Path) -> None:
            lock_held.append(lock.locked())
            await real_persist(raw, target)

        monkeypatch.setattr(artifacts_module, "persist_metadata", spy_persist)
        service = SessionArtifactService(
            session_id=SESSION_ID, session_dir=session_dir, metadata_lock=lock
        )

        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        assert lock_held == [True]
        metadata = SessionLoader.load_metadata(session_dir)
        assert len(metadata.artifacts) == 1

    @pytest.mark.asyncio
    async def test_persisted_shape_matches_contract(
        self, service: SessionArtifactService, session_dir: Path
    ):
        """The persisted JSON carries the Code Web contract field names."""
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        raw = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        (entry,) = raw["artifacts"]
        assert entry["kind"] == "pull_request"
        assert entry["provider"] == "github"
        assert entry["session_id"] == SESSION_ID
        assert entry["external_key"] == "github.com/mistralai/vibe/pull/123"
        assert entry["external_url"] == "https://github.com/mistralai/vibe/pull/123"
        assert entry["display_title"] is None
        assert entry["viewed_at"] is None
        details = entry["details"]
        assert details["kind"] == "pull_request"
        assert details["schema_version"] == 1
        assert details["repo_host"] == "github.com"
        assert details["repo_owner"] == "mistralai"
        assert details["repo_name"] == "vibe"
        assert details["pull_request_number"] == 123
        assert details["state"] == "open"


def _require_session(session_id: str) -> None:
    if session_id != SESSION_ID:
        raise SessionBackendError(
            ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
        )


class TestToPublicArtifact:
    @pytest.mark.asyncio
    async def test_github(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        public = to_public_artifact(artifact)
        assert public.id == artifact.id
        assert public.session_id == SESSION_ID
        assert public.kind == "pull_request"
        assert public.provider == "github"
        assert public.external_key == "github.com/mistralai/vibe/pull/123"
        assert public.external_url == "https://github.com/mistralai/vibe/pull/123"
        assert public.display_title is None
        assert public.viewed_at is None
        assert isinstance(public.details, PublicGitHubPullRequestDetails)
        assert public.details.repo_host == "github.com"
        assert public.details.repo_owner == "mistralai"
        assert public.details.repo_name == "vibe"
        assert public.details.pull_request_number == 123
        assert public.details.state == "open"

    @pytest.mark.asyncio
    async def test_gitlab(self, service: SessionArtifactService, monkeypatch):
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=4242),
        )
        [artifact] = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )
        public = to_public_artifact(artifact)
        assert public.provider == "gitlab"
        assert isinstance(public.details, PublicGitLabMergeRequestDetails)
        assert public.details.repo_id == 4242
        assert public.details.project_path == "team/project"
        assert public.details.merge_request_iid == 7

    @pytest.mark.asyncio
    async def test_wire_dump_is_camel_case(self, service: SessionArtifactService):
        """The serialized wire form uses the Code Web contract's camelCase keys."""
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        wire = to_public_artifact(artifact).model_dump(mode="json", by_alias=True)
        assert wire["sessionId"] == SESSION_ID
        assert wire["externalKey"] == "github.com/mistralai/vibe/pull/123"
        assert wire["externalUrl"] == "https://github.com/mistralai/vibe/pull/123"
        assert wire["displayTitle"] is None
        assert wire["discoveredAt"] == artifact.discovered_at
        assert wire["viewedAt"] is None
        details = wire["details"]
        assert details["schemaVersion"] == 1
        assert details["repoHost"] == "github.com"
        assert details["repoOwner"] == "mistralai"
        assert details["repoName"] == "vibe"
        assert details["pullRequestNumber"] == 123

    @pytest.mark.asyncio
    async def test_raw_status_fields_reach_the_wire(
        self, service: SessionArtifactService, github: _GitHub
    ):
        """The raw provider-neutral statuses ride the wire projection."""
        github.snapshot = _snapshot(
            state="open",
            checks=ChecksSummary(state="fail"),
            review_decision="APPROVED",
            merge_status="blocked",
            optional_checks_state="pass",
        )
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )

        wire = to_public_artifact(artifact).model_dump(mode="json", by_alias=True)
        assert wire["details"]["prStatus"] == "action_needed"
        assert wire["details"]["prStatusReason"] == "checks_failing"
        assert wire["details"]["checksStatus"] == "failing"
        assert wire["details"]["optionalChecksStatus"] == "passing"
        assert wire["details"]["reviewDecision"] == "approved"
        assert wire["details"]["mergeStatus"] == "blocked"
        assert wire["details"]["providerUpdatedAt"] is not None
        # The status is a read of the provider, not session state.
        stored = json.loads((service._session_dir / "meta.json").read_text())
        assert stored["artifacts"][0]["details"]["checks_status"] is None

    @pytest.mark.asyncio
    async def test_details_none(self, service: SessionArtifactService):
        """An artifact stored without details projects without details."""
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        stripped = artifact.model_copy(update={"details": None})
        assert to_public_artifact(stripped).details is None


class TestArtifactRequestHandler:
    @pytest.mark.asyncio
    async def test_list_with_service(self, service: SessionArtifactService):
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        handler = ArtifactRequestHandler(service, _require_session)
        result = await handler.dispatch("artifacts/list", {"sessionId": SESSION_ID})
        assert isinstance(result.response, ArtifactsListResponse)
        artifacts = result.response.artifacts
        assert len(artifacts) == 1
        assert artifacts[0].provider == "github"
        assert artifacts[0].external_key == "github.com/mistralai/vibe/pull/123"

    @pytest.mark.asyncio
    async def test_list_without_service_answers_empty(self):
        """Feature flag off → the namespace exists and answers empty."""
        handler = ArtifactRequestHandler(None, _require_session)
        result = await handler.dispatch("artifacts/list", {"sessionId": SESSION_ID})
        assert isinstance(result.response, ArtifactsListResponse)
        assert result.response.artifacts == []

    @pytest.mark.asyncio
    async def test_list_rejects_other_session(self, service: SessionArtifactService):
        handler = ArtifactRequestHandler(service, _require_session)
        with pytest.raises(SessionBackendError):
            await handler.dispatch("artifacts/list", {"sessionId": "another-session"})

    @pytest.mark.asyncio
    async def test_unknown_method(self, service: SessionArtifactService):
        handler = ArtifactRequestHandler(service, _require_session)
        with pytest.raises(RequestFailure):
            await handler.dispatch("artifacts/nope", {"sessionId": SESSION_ID})

    @pytest.mark.asyncio
    async def test_mark_viewed_marks_persists_and_emits(
        self, service: SessionArtifactService, session_dir: Path
    ):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        emitted: list[SessionArtifact] = []

        async def emit_updated(updated: SessionArtifact) -> None:
            emitted.append(updated)

        handler = ArtifactRequestHandler(
            service, _require_session, emit_updated=emit_updated
        )
        result = await handler.dispatch(
            "artifacts/markViewed",
            {"sessionId": SESSION_ID, "artifactIds": [artifact.id]},
        )
        assert isinstance(result.response, EmptyResponse)

        (updated,) = service.list_artifacts()
        assert updated.viewed_at is not None
        assert [a.id for a in emitted] == [artifact.id]
        assert emitted[0].viewed_at == updated.viewed_at

        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert on_disk["artifacts"][0]["viewed_at"] == updated.viewed_at

    @pytest.mark.asyncio
    async def test_mark_viewed_idempotent_keeps_original_viewed_at(
        self, service: SessionArtifactService
    ):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        emitted: list[SessionArtifact] = []

        async def emit_updated(updated: SessionArtifact) -> None:
            emitted.append(updated)

        handler = ArtifactRequestHandler(
            service, _require_session, emit_updated=emit_updated
        )
        params = {"sessionId": SESSION_ID, "artifactIds": [artifact.id]}
        await handler.dispatch("artifacts/markViewed", params)
        (first,) = service.list_artifacts()
        assert first.viewed_at is not None

        result = await handler.dispatch("artifacts/markViewed", params)
        assert isinstance(result.response, EmptyResponse)
        (second,) = service.list_artifacts()
        assert second.viewed_at == first.viewed_at
        assert len(emitted) == 1  # already viewed: no new update announced

    @pytest.mark.asyncio
    async def test_mark_viewed_ignores_unknown_ids(
        self, service: SessionArtifactService
    ):
        """Cloud parity: markViewedByIds runs with ignoreNotFound."""
        emitted: list[SessionArtifact] = []

        async def emit_updated(updated: SessionArtifact) -> None:
            emitted.append(updated)

        handler = ArtifactRequestHandler(
            service, _require_session, emit_updated=emit_updated
        )
        result = await handler.dispatch(
            "artifacts/markViewed",
            {"sessionId": SESSION_ID, "artifactIds": ["no-such-artifact"]},
        )
        assert isinstance(result.response, EmptyResponse)
        assert emitted == []

    @pytest.mark.asyncio
    async def test_mark_viewed_without_service_answers_empty(self):
        """Feature flag off → the write no-ops instead of failing."""
        handler = ArtifactRequestHandler(None, _require_session)
        result = await handler.dispatch(
            "artifacts/markViewed", {"sessionId": SESSION_ID, "artifactIds": ["any"]}
        )
        assert isinstance(result.response, EmptyResponse)

    @pytest.mark.asyncio
    async def test_mark_viewed_rejects_other_session(
        self, service: SessionArtifactService
    ):
        handler = ArtifactRequestHandler(service, _require_session)
        with pytest.raises(SessionBackendError):
            await handler.dispatch(
                "artifacts/markViewed",
                {"sessionId": "another-session", "artifactIds": ["any"]},
            )


def _projection() -> ClientProjection:
    return ClientProjection(
        PublicSessionState(
            event_id=0,
            session=PublicSession(
                id=SESSION_ID, status=IdleSessionStatus(), created_at=1, updated_at=1
            ),
            history=[],
            turns=[],
            active_callbacks=[],
        )
    )


def _artifact_notification(method: str, artifact, sequence: int) -> Notification:
    params = ArtifactCreatedParams(
        event_id=sequence,
        session_id=SESSION_ID,
        emitted_at=1_760_000_000_000,
        artifact=artifact,
    )
    return Notification(
        method=method, params=params.model_dump(mode="json", by_alias=True)
    )


class TestArtifactEvents:
    @pytest.mark.asyncio
    async def test_parse_artifacts_created(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        notification = _artifact_notification(
            "artifacts/created", to_public_artifact(artifact), sequence=1
        )
        params = _parse_event_params(notification)
        assert isinstance(params, ArtifactCreatedParams)
        assert params.artifact.external_key == "github.com/mistralai/vibe/pull/123"

    @pytest.mark.asyncio
    async def test_projection_consume_created(self, service: SessionArtifactService):
        """The sequenced event stream reduces artifacts/created to ArtifactCreated."""
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        projection = _projection()
        event = projection.consume(
            _artifact_notification(
                "artifacts/created", to_public_artifact(artifact), sequence=1
            )
        )
        assert isinstance(event, ArtifactCreated)
        assert event.artifact.provider == "github"
        assert projection.last_event_id == 1

    @pytest.mark.asyncio
    async def test_projection_consume_updated(self, service: SessionArtifactService):
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        public = to_public_artifact(artifact).model_copy(
            update={"viewed_at": "2026-10-13T11:00:00+00:00"}
        )
        projection = _projection()
        event = projection.consume(
            _artifact_notification("artifacts/updated", public, 1)
        )
        assert isinstance(event, ArtifactUpdated)
        assert event.artifact.viewed_at == "2026-10-13T11:00:00+00:00"

    @pytest.mark.asyncio
    async def test_projection_enforces_sequence(self, service: SessionArtifactService):
        """Artifact notifications participate in the session event sequence."""
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        projection = _projection()
        # A gap (sequence 2 with nothing at 1) is a sequence error, and the
        # client resyncs — same as any other sequenced event.
        from vibe.app_server.events import EventSequenceError

        with pytest.raises(EventSequenceError):
            projection.consume(
                _artifact_notification(
                    "artifacts/created", to_public_artifact(artifact), sequence=2
                )
            )


# ---------------------------------------------------------------------------
# Harness adapter integration: shell-effect interception, drain, notification.
# ---------------------------------------------------------------------------


def _shell_effect_entry(
    effect_id: str,
    command: str,
    *,
    tool_name: str = "bash",
    status: str = "completed",
    stdout: str = "",
    stderr: str = "",
    output: object = None,
) -> dict[str, Any]:
    """A raw history effect carrying a shell command, as the Harness emits it."""
    if output is None:
        output = {
            "structured_content": {
                "stdout": stdout,
                "stderr": stderr,
                "output": stdout,
                "returncode": 0,
            }
        }
    return {
        "type": "effect",
        "id": effect_id,
        "detail": {
            "kind": "tool",
            "toolName": tool_name,
            "input": {"command": command},
        },
        "state": {"status": status, "output": output},
    }


def _state_with_entries(*entries: object) -> SimpleNamespace:
    """A session-state double exposing only what the collectors read."""
    return SimpleNamespace(history=SimpleNamespace(entries=list(entries)))


def _pending_effect(
    effect_id: str,
    command: str,
    stdout: str = "",
    stderr: str = "",
    status: str = "completed",
) -> Any:
    from vibe.app_server._unified_harness_backend_adapter import _PendingShellEffect

    return _PendingShellEffect(
        effect_id=effect_id,
        command=command,
        stdout=stdout,
        stderr=stderr,
        status=status,
    )


def _github_artifact(*, source_branch: str = "") -> GitHubPullRequestArtifact:
    return GitHubPullRequestArtifact(
        id="01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a5c",
        session_id=SESSION_ID,
        external_key="github.com/mistralai/vibe/pull/123",
        external_url="https://github.com/mistralai/vibe/pull/123",
        display_title="Fix login",
        discovered_at="2026-10-13T10:05:00+00:00",
        details=GitHubPullRequestDetails(
            repo_host="github.com",
            repo_owner="mistralai",
            repo_name="vibe",
            pull_request_number=123,
            state="open",
            source_branch=source_branch,
            target_branch="",
            author="",
            provider_created_at=None,
            provider_updated_at=None,
            provider_closed_at=None,
            provider_merged_at=None,
            merge_commit_sha=None,
            pr_status=None,
            checks=None,
        ),
    )


def _gitlab_artifact(*, repo_id: int | None = None) -> GitLabMergeRequestArtifact:
    """A Phase 1-detected GitLab MR artifact (repo_id resolved or not)."""
    external_key = (
        f"gitlab.com/{repo_id}/merge_requests/7"
        if repo_id is not None
        else "gitlab.com/team/project/-/merge_requests/7"
    )
    return GitLabMergeRequestArtifact(
        id="01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a5d",
        session_id=SESSION_ID,
        external_key=external_key,
        external_url="https://gitlab.com/team/project/-/merge_requests/7",
        display_title="Add feature",
        discovered_at="2026-10-13T10:05:00+00:00",
        details=GitLabMergeRequestDetails(
            repo_host="gitlab.com",
            repo_id=repo_id,
            project_path="team/project",
            merge_request_iid=7,
            state="open",
            source_branch="",
            target_branch="",
            author="",
            provider_created_at=None,
            provider_updated_at=None,
            provider_closed_at=None,
            provider_merged_at=None,
            merge_commit_sha=None,
            pr_status=None,
            checks=None,
        ),
    )


class _ArtifactSession:
    """The Harness session surface the adapter touches at construction time."""

    def __init__(
        self, session_id: str, cwd: str | None = None, *, ephemeral: bool = True
    ) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.parent_session_id: str | None = None
        self.ephemeral = ephemeral
        self.active_turn_id: str | None = None
        self.settle_configuration: Any = None

    def configure_turn_settlement(self, settle: Any) -> None:
        self.settle_configuration = settle

    def configure_waiting_turn_steer(self, steered: Any) -> None:
        """Unused: no queued input joins a waiting turn here."""

    def pin(self, _pin: Any) -> None:
        return None

    async def read(self, _params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            IdleSessionStatus,
            LatestPublicHistoryPage,
            PublicSession as HarnessPublicSession,
            PublicSessionState as HarnessPublicSessionState,
            SessionSnapshot as HarnessSessionSnapshot,
            TurnQueue as HarnessTurnQueue,
        )

        return SimpleNamespace(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        preview="",
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    history=LatestPublicHistoryPage(entries=[]),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=getattr(_params, "history_limit", 1),
                watermark=0,
            )
        )


def _artifact_adapter(
    tmp_path: Path,
    *,
    pr_monitoring: bool = True,
    cwd: str | None = None,
    services: FakeSessionBackendServices | None = None,
    ephemeral: bool = True,
) -> Any:
    """A real UnifiedHarnessBackendAdapter over inert context doubles.

    Mirrors the inert adapter of test_unified_harness_backend_adapter with the
    ``enable_pr_monitoring`` setting (gates both Phase 1 detection
    and Phase 2 discovery) and the session cwd configurable; the artifact
    service under test is real.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import LocalRuntimeAdapterConfig
    from mistralai_vibe_local_harness.vibe._host import _core_config
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )

    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(enable_pr_monitoring=pr_monitoring)
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        requested_plugins=(),
        config_orchestrator=orchestrator,
        harness_files=HarnessFilesManager(
            sources=(), trust_store=TrustedFoldersManager()
        ).for_session(Path(cwd) if cwd is not None else tmp_path),
        agents=cast(Any, None),
        derive=cast(Any, None),
        permissions=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        plugin_mcp=PluginMCPCatalog(MCPRegistry(), MCPAuthenticationService()),
        mcp_cache_root=str(tmp_path / "mcp-descriptors"),
        mcp_discovery_flights=fake_process_services().mcp_discovery.flights(),
        mcp_enable_system_trust_store=False,
        org_config_service=fake_process_services().org_config,
        experiments_service=fake_process_services().experiments,
        model_catalog_service=fake_process_services().model_catalog,
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=cast(Any, object()),
        core_config=_core_config(SESSION_ID),
        adapter_config=LocalRuntimeAdapterConfig(),
        skill_payloads={},
        registry_versions={},
    )
    return UnifiedHarnessBackendAdapter(
        cast(Any, _ArtifactSession(SESSION_ID, cwd=cwd, ephemeral=ephemeral)),
        context,
        derivation,
        services=services,
    )


class TestTerminalShellEffect:
    """The raw-entry extractor feeding artifact detection."""

    def test_extracts_shell_effect_with_structured_content(self) -> None:
        """*Prepare*: a completed bash effect with nested tool output.
        *Do*: Run it through ``_terminal_shell_effect``.
        *Assert*: id, command, stdout, stderr and status survive the extraction.
        """
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        entry = _shell_effect_entry(
            "effect-1", GH_COMMAND, stdout=GH_STDOUT, stderr="warning"
        )
        effect = _terminal_shell_effect(entry)
        assert effect is not None
        assert effect.effect_id == "effect-1"
        assert effect.command == GH_COMMAND
        assert effect.stdout == GH_STDOUT
        assert effect.stderr == "warning"
        assert effect.status == "completed"

    @pytest.mark.parametrize(
        "tool_name", ["bash", "file_system.bash", "shell", "git_bash", "powershell"]
    )
    def test_accepts_every_shell_tool_name(self, tool_name: str) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        effect = _terminal_shell_effect(
            _shell_effect_entry("effect-1", GH_COMMAND, tool_name=tool_name)
        )
        assert effect is not None

    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "skipped"])
    def test_accepts_every_terminal_state(self, status: str) -> None:
        """Collection keeps every terminal state; the service filters statuses."""
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        effect = _terminal_shell_effect(
            _shell_effect_entry("effect-1", GH_COMMAND, status=status)
        )
        assert effect is not None
        assert effect.status == status

    def test_skips_non_shell_tool(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        entry = _shell_effect_entry("effect-1", GH_COMMAND, tool_name="read_file")
        assert _terminal_shell_effect(entry) is None

    def test_skips_non_effect_entry(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        assert _terminal_shell_effect({"type": "message", "id": "m-1"}) is None
        assert _terminal_shell_effect("not a mapping") is None

    def test_skips_non_terminal_state(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        entry = _shell_effect_entry("effect-1", GH_COMMAND, status="running")
        assert _terminal_shell_effect(entry) is None

    def test_skips_missing_or_malformed_command(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        def entry_with(tool_input: object) -> dict[str, object]:
            return {
                "type": "effect",
                "id": "effect-1",
                "detail": {"kind": "tool", "toolName": "bash", "input": tool_input},
                "state": {"status": "completed", "output": {}},
            }

        assert _terminal_shell_effect(entry_with({})) is None
        assert _terminal_shell_effect(entry_with("ls")) is None
        assert _terminal_shell_effect(entry_with({"command": 42})) is None

    def test_falls_back_to_state_output_text(self) -> None:
        """A failed compound command keeps the created URL in ``outputText``."""
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        entry = _shell_effect_entry("effect-1", GH_COMMAND, status="failed", output={})
        entry["state"]["outputText"] = GH_STDOUT
        effect = _terminal_shell_effect(entry)
        assert effect is not None
        assert effect.stdout == GH_STDOUT
        assert effect.status == "failed"

    def test_output_text_not_duplicated_when_already_in_stdout(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _terminal_shell_effect,
        )

        entry = _shell_effect_entry("effect-1", GH_COMMAND, stdout=GH_STDOUT)
        entry["state"]["outputText"] = GH_STDOUT
        effect = _terminal_shell_effect(entry)
        assert effect is not None
        assert effect.stdout == GH_STDOUT


class TestShellEffectOutputTexts:
    """stdout/stderr extraction from the shell effect's output envelope."""

    def test_structured_content_preferred_over_flat_keys(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _shell_effect_output_texts,
        )

        output = {
            "structured_content": {"stdout": "structured", "stderr": "err"},
            "stdout": "flat",
        }
        assert _shell_effect_output_texts(output) == ("structured", "err")

    def test_flat_keys_fallback(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _shell_effect_output_texts,
        )

        assert _shell_effect_output_texts({"stdout": "out", "stderr": "err"}) == (
            "out",
            "err",
        )

    def test_transcript_fills_stdout(self) -> None:
        """A transcript-only output still surfaces the command's text."""
        from vibe.app_server._unified_harness_backend_adapter import (
            _shell_effect_output_texts,
        )

        assert _shell_effect_output_texts({"output": "transcript"}) == (
            "transcript",
            "",
        )

    def test_non_mapping_output_yields_empty_texts(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _shell_effect_output_texts,
        )

        assert _shell_effect_output_texts("raw text") == ("", "")
        assert _shell_effect_output_texts(None) == ("", "")

    def test_non_string_fields_ignored(self) -> None:
        from vibe.app_server._unified_harness_backend_adapter import (
            _shell_effect_output_texts,
        )

        assert _shell_effect_output_texts({"stdout": 42, "stderr": None}) == ("", "")


class TestCollectPendingShellEffects:
    """The sync collector inside ``_updated_state``."""

    @pytest.mark.asyncio
    async def test_collects_new_terminal_shell_effects(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path)
        state = _state_with_entries(
            _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT),
            _shell_effect_entry("e-2", "ls -la", tool_name="file_system.bash"),
            _shell_effect_entry("e-3", "cat file", tool_name="read_file"),
            _shell_effect_entry("e-4", "ls", status="running"),
        )
        adapter._collect_pending_shell_effects(state)
        assert [e.effect_id for e in adapter._pending_shell_effects] == ["e-1", "e-2"]
        assert adapter._processed_effect_ids == {"e-1", "e-2"}

    @pytest.mark.asyncio
    async def test_reconciling_the_same_snapshot_twice_dedups(
        self, tmp_path: Path
    ) -> None:
        adapter = _artifact_adapter(tmp_path)
        state = _state_with_entries(
            _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        adapter._collect_pending_shell_effects(state)
        adapter._collect_pending_shell_effects(state)
        assert len(adapter._pending_shell_effects) == 1

    @pytest.mark.asyncio
    async def test_flag_off_collects_nothing(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path, pr_monitoring=False)
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT)
            )
        )
        assert adapter._pending_shell_effects == []
        assert adapter._processed_effect_ids == set()

    @pytest.mark.asyncio
    async def test_sandboxed_session_collects_nothing(self, tmp_path: Path) -> None:
        """Local only, like discovery: the provider reads would run on the host."""
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True)
        adapter._adapter_config = cast(Any, SimpleNamespace(sandbox=object()))
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT)
            )
        )
        assert adapter._pending_shell_effects == []
        assert adapter._processed_effect_ids == set()

    @pytest.mark.asyncio
    async def test_collects_output_text_fallback(self, tmp_path: Path) -> None:
        """The state's combined transcript feeds detection when output is empty."""
        adapter = _artifact_adapter(tmp_path)
        entry = _shell_effect_entry("e-1", GH_COMMAND, status="failed", output={})
        entry["state"]["outputText"] = GH_STDOUT
        adapter._collect_pending_shell_effects(_state_with_entries(entry))
        assert len(adapter._pending_shell_effects) == 1
        assert adapter._pending_shell_effects[0].stdout == GH_STDOUT


class TestDrainPendingShellEffects:
    """The async drain: detection, metadata sync, notification."""

    @pytest.mark.asyncio
    async def test_creates_artifact_and_notifies(self, tmp_path: Path) -> None:
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        artifacts = adapter._artifact_service.list_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0].external_key == "github.com/mistralai/vibe/pull/123"
        assert len(services.notifications) == 1
        method, params = services.notifications[0]
        assert method == "artifacts/created"
        assert isinstance(params, ArtifactCreatedParams)
        assert params.session_id == SESSION_ID
        assert params.artifact.external_key == "github.com/mistralai/vibe/pull/123"
        assert adapter._pending_shell_effects == []

    @pytest.mark.asyncio
    async def test_collect_then_drain_detects_github_pr(self, tmp_path: Path) -> None:
        """The interception wiring end to end: history entry → artifact + event."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT)
            )
        )
        await adapter._drain_pending_shell_effects()
        assert len(adapter._artifact_service.list_artifacts()) == 1
        assert [method for method, _ in services.notifications] == ["artifacts/created"]

    @pytest.mark.asyncio
    async def test_turning_monitoring_off_releases_the_watch(
        self, tmp_path: Path, github: _GitHub
    ) -> None:
        """An org config or a reload that turns it off stops the polling of
        the PRs the session already watches, at the next refresh.
        """
        from vibe.app_server._pull_request_cache import pull_request_cache

        adapter = _artifact_adapter(tmp_path)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        try:
            await adapter._drain_pending_shell_effects()
            assert adapter in pull_request_cache()._owners

            await adapter._context.config_orchestrator.set_field(
                "/enable_pr_monitoring", False, target_layer="overrides"
            )
            adapter.refresh_pull_requests(full=True)

            assert adapter not in pull_request_cache()._owners
            assert not adapter._watching_pull_requests
            # The cache's passes only read PRs still in ``_details``.
            watched = [key for key, _details in adapter._artifact_service.watched()]
            assert watched
            assert not any(key in pull_request_cache()._details for key in watched)
        finally:
            await _close_cache()

    @pytest.mark.asyncio
    async def test_turning_monitoring_back_on_restarts_the_watch(
        self, tmp_path: Path, github: _GitHub
    ) -> None:
        """A turn end or ``artifacts/sync`` after it is back on watches the
        session's PRs again, without waiting for a new PR or a resume.
        """
        from vibe.app_server._pull_request_cache import pull_request_cache

        adapter = _artifact_adapter(tmp_path)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        orchestrator = adapter._context.config_orchestrator
        try:
            await adapter._drain_pending_shell_effects()
            await orchestrator.set_field(
                "/enable_pr_monitoring", False, target_layer="overrides"
            )
            adapter.refresh_pull_requests(full=True)
            assert adapter not in pull_request_cache()._owners

            await orchestrator.set_field(
                "/enable_pr_monitoring", True, target_layer="overrides"
            )
            adapter.refresh_pull_requests(full=False)

            assert adapter._watching_pull_requests
            assert adapter in pull_request_cache()._owners
            watched = [key for key, _details in adapter._artifact_service.watched()]
            assert watched
            assert all(key in pull_request_cache()._details for key in watched)
        finally:
            await _close_cache()

    @pytest.mark.asyncio
    async def test_collect_then_drain_detects_gitlab_mr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=None),
        )
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GLAB_COMMAND, stdout=GLAB_STDOUT)
            )
        )
        await adapter._drain_pending_shell_effects()
        artifacts = adapter._artifact_service.list_artifacts()
        assert len(artifacts) == 1
        assert isinstance(artifacts[0], GitLabMergeRequestArtifact)
        assert artifacts[0].external_key == (
            "gitlab.com/team/project/-/merge_requests/7"
        )

    @pytest.mark.asyncio
    async def test_syncs_adapter_metadata_and_meta_json(self, tmp_path: Path) -> None:
        """A resumed session (meta.json present) carries artifacts forward."""
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir)
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        assert adapter._metadata is not None
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._metadata is not None
        keys = [a.external_key for a in adapter._metadata.artifacts]
        assert keys == ["github.com/mistralai/vibe/pull/123"]
        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert [a["external_key"] for a in on_disk["artifacts"]] == keys

    @pytest.mark.asyncio
    async def test_without_metadata_still_notifies(self, tmp_path: Path) -> None:
        """A fresh session (no meta.json yet) still records and notifies."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        assert adapter._metadata is None
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._metadata is None
        assert len(adapter._artifact_service.list_artifacts()) == 1
        assert [method for method, _ in services.notifications] == ["artifacts/created"]

    @pytest.mark.asyncio
    async def test_first_persist_keeps_artifacts_recorded_before_metadata(
        self, tmp_path: Path
    ) -> None:
        """Artifacts detected before meta.json must survive the first sidecar write."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services, ephemeral=False)
        assert adapter._metadata is None
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._metadata is None
        assert len(adapter._artifact_service.list_artifacts()) == 1

        metadata = await adapter._persist_session_metadata()
        assert [a.external_key for a in metadata.artifacts] == [
            "github.com/mistralai/vibe/pull/123"
        ]
        on_disk = json.loads(
            (tmp_path / "unified" / SESSION_ID / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        assert [a["external_key"] for a in on_disk["artifacts"]] == [
            "github.com/mistralai/vibe/pull/123"
        ]

    @pytest.mark.asyncio
    async def test_flag_off_drops_the_queue(self, tmp_path: Path) -> None:
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, pr_monitoring=False, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._pending_shell_effects == []
        assert adapter._artifact_service.list_artifacts() == []
        assert services.notifications == []

    @pytest.mark.asyncio
    async def test_non_pr_command_notifies_nothing(self, tmp_path: Path) -> None:
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", "ls -la", stdout="file.txt\n")
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._artifact_service.list_artifacts() == []
        assert services.notifications == []

    @pytest.mark.asyncio
    async def test_failed_effect_with_url_is_processed(self, tmp_path: Path) -> None:
        """gh exits non-zero after printing the URL in some flows."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT, status="failed")
        )
        await adapter._drain_pending_shell_effects()
        assert len(adapter._artifact_service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_cancelled_effect_is_ignored(self, tmp_path: Path) -> None:
        """Collected (terminal) but not processable: the service filters it."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT, status="cancelled")
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._artifact_service.list_artifacts() == []
        assert services.notifications == []

    @pytest.mark.asyncio
    async def test_duplicate_pr_is_not_notified_twice(self, tmp_path: Path) -> None:
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        adapter._pending_shell_effects.append(
            _pending_effect("e-2", GH_COMMAND, stdout=GH_STDOUT)
        )
        await adapter._drain_pending_shell_effects()
        assert len(adapter._artifact_service.list_artifacts()) == 1
        assert [method for method, _ in services.notifications] == ["artifacts/created"]

    @pytest.mark.asyncio
    async def test_detection_failure_is_logged_and_skipped(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A detector crash never breaks session event processing."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._pending_shell_effects.append(
            _pending_effect("e-1", GH_COMMAND, stdout=GH_STDOUT)
        )

        async def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("detector exploded")

        monkeypatch.setattr(adapter._artifact_service, "on_shell_effect", boom)
        with caplog.at_level(logging.WARNING, logger="vibe"):
            await adapter._drain_pending_shell_effects()
        assert adapter._artifact_service.list_artifacts() == []
        assert services.notifications == []
        assert adapter._pending_shell_effects == []
        assert any(
            "Artifact detection failed" in record.getMessage()
            for record in caplog.records
        )


class TestSeedArtifactEffects:
    """Resume seeding: a re-scanned history is not reprocessed."""

    @pytest.mark.asyncio
    async def test_resumed_history_is_not_recollected(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path)
        history = _state_with_entries(
            _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT),
            _shell_effect_entry("e-2", "ls -la"),
            {"type": "message", "id": "m-1"},
        )
        adapter._seed_artifact_effects(history)
        assert adapter._processed_effect_ids == {"e-1", "e-2"}
        adapter._collect_pending_shell_effects(history)
        assert adapter._pending_shell_effects == []

    @pytest.mark.asyncio
    async def test_effects_arriving_after_resume_are_collected(
        self, tmp_path: Path
    ) -> None:
        adapter = _artifact_adapter(tmp_path)
        adapter._seed_artifact_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT)
            )
        )
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT),
                _shell_effect_entry("e-2", GLAB_COMMAND, stdout=GLAB_STDOUT),
            )
        )
        assert [e.effect_id for e in adapter._pending_shell_effects] == ["e-2"]


class TestBuildUnifiedSessionMetadata:
    """The metadata carry-over that keeps artifacts across full persists."""

    @staticmethod
    def _harness_session() -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            IdleSessionStatus,
            PublicSession as HarnessPublicSession,
        )

        return HarnessPublicSession(
            id=SESSION_ID,
            status=IdleSessionStatus(),
            created_at=1_760_000_000_000,
            updated_at=1_760_000_100_000,
            title="Session title",
        )

    def test_artifacts_carried_over_from_previous(self) -> None:
        pytest.importorskip("mistralai_vibe_local_harness.vibe")
        from vibe.app_server._unified_harness_backend_adapter import (
            _build_unified_session_metadata,
        )

        artifact = _github_artifact()
        previous = SessionMetadata(
            session_id=SESSION_ID,
            start_time="2026-10-13T10:00:00+00:00",
            end_time=None,
            git_commit=None,
            git_branch=None,
            environment={},
            username="tester",
            artifacts=[artifact],
        )
        result = _build_unified_session_metadata(
            self._harness_session(), "/tmp/cwd", previous=previous
        )
        assert result.artifacts == [artifact]

    def test_artifacts_default_to_empty_without_previous(self) -> None:
        pytest.importorskip("mistralai_vibe_local_harness.vibe")
        from vibe.app_server._unified_harness_backend_adapter import (
            _build_unified_session_metadata,
        )

        result = _build_unified_session_metadata(
            self._harness_session(), "/tmp/cwd", previous=None
        )
        assert result.artifacts == []


class TestAdapterDispatchArtifacts:
    """The ``artifacts/`` namespace routed through a real adapter."""

    @pytest.mark.asyncio
    async def test_list_answers_persisted_artifacts(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir, artifacts=[_github_artifact()])
        adapter = _artifact_adapter(tmp_path)
        result = await adapter.dispatch_extension(
            "artifacts/list", {"sessionId": SESSION_ID}
        )
        artifacts = result.response.artifacts
        assert len(artifacts) == 1
        assert artifacts[0].provider == "github"
        assert artifacts[0].external_key == "github.com/mistralai/vibe/pull/123"

    @pytest.mark.asyncio
    async def test_list_rejects_other_session(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path)
        with pytest.raises(SessionBackendError):
            await adapter.dispatch_extension(
                "artifacts/list", {"sessionId": "another-session"}
            )

    @pytest.mark.asyncio
    async def test_mark_viewed_answers_and_notifies(self, tmp_path: Path) -> None:
        """markViewed persists viewed_at and announces artifacts/updated."""
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        artifact = _github_artifact()
        _write_metadata(session_dir, artifacts=[artifact])
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)

        result = await adapter.dispatch_extension(
            "artifacts/markViewed",
            {"sessionId": SESSION_ID, "artifactIds": [artifact.id]},
        )
        assert isinstance(result.response, EmptyResponse)

        (method, params) = services.notifications[-1]
        assert method == "artifacts/updated"
        assert isinstance(params, ArtifactUpdatedParams)
        assert params.artifact.id == artifact.id
        assert params.artifact.viewed_at is not None

        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert on_disk["artifacts"][0]["viewed_at"] is not None

        listed = await adapter.dispatch_extension(
            "artifacts/list", {"sessionId": SESSION_ID}
        )
        assert listed.response.artifacts[0].viewed_at is not None


def _artifact_host(
    tmp_path: Path, *, pr_monitoring: bool | Callable[[], bool] = False
) -> Any:
    """A real host adapter over an inert harness, rooted at ``tmp_path``.

    The cold artifact path reads ``<storage_root>/unified/<session_id>/
    meta.json`` through the read context, so the only context field that
    matters is the storage root. ``pr_monitoring`` may be a callable, read at
    each session read, to change the setting between reads.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
        UnifiedReadContext,
    )

    async def build_read_context(options: Any) -> UnifiedReadContext:
        enabled = pr_monitoring() if callable(pr_monitoring) else pr_monitoring
        return UnifiedReadContext(
            storage_root=str(tmp_path),
            config_orchestrator=FakeConfigOrchestrator(
                build_test_vibe_config(enable_pr_monitoring=enabled)
            ),
            harness_files=HarnessFilesManager(
                sources=(), trust_store=TrustedFoldersManager()
            ).for_session(tmp_path),
            legacy_source_loader=cast(Any, None),
            legacy_source_resolver=cast(Any, None),
            build_agents=cast(Any, None),
        )

    return UnifiedHarnessBackendHostAdapter(
        cast(Any, Mock()), cast(Any, None), build_read_context=build_read_context
    )


class TestHostArtifactsColdPath:
    """Host-level ``artifacts/`` answers for sessions with no attached runtime.

    Desktop's catalogue channel has no session attached; it must still list
    (and mark viewed) a stored session's persisted artifacts.
    """

    @pytest.mark.asyncio
    async def test_list_reads_persisted_artifacts(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir, artifacts=[_github_artifact()])
        host = _artifact_host(tmp_path)

        response = await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
        assert len(response.artifacts) == 1
        assert (
            response.artifacts[0].external_key == "github.com/mistralai/vibe/pull/123"
        )

    @pytest.mark.asyncio
    async def test_list_unknown_session_answers_empty(self, tmp_path: Path) -> None:
        host = _artifact_host(tmp_path)

        response = await host.list_artifacts(
            ArtifactsListParams(session_id="no-such-session")
        )
        assert response.artifacts == []

    @pytest.mark.asyncio
    async def test_mark_viewed_persists_to_metadata(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        artifact = _github_artifact()
        _write_metadata(session_dir, artifacts=[artifact])
        host = _artifact_host(tmp_path)

        await host.mark_artifacts_viewed(
            ArtifactsMarkViewedParams(session_id=SESSION_ID, artifact_ids=[artifact.id])
        )
        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert on_disk["artifacts"][0]["viewed_at"] is not None

        # Unknown ids and unknown sessions are a silent no-op (cloud parity).
        await host.mark_artifacts_viewed(
            ArtifactsMarkViewedParams(
                session_id=SESSION_ID, artifact_ids=["no-such-artifact"]
            )
        )
        await host.mark_artifacts_viewed(
            ArtifactsMarkViewedParams(
                session_id="no-such-session", artifact_ids=[artifact.id]
            )
        )

    @pytest.mark.asyncio
    async def test_concurrent_marks_keep_every_update(self, tmp_path: Path) -> None:
        """Two concurrent cold-path marks cannot lose each other's update.

        Each call builds a fresh service over ``meta.json``; without a
        per-session lock, the second call's read-modify-write would overwrite
        the first call's ``viewed_at``.
        """
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        first = _github_artifact()
        second = first.model_copy(
            update={
                "id": "01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a5e",
                "external_key": "github.com/mistralai/vibe/pull/124",
            }
        )
        _write_metadata(session_dir, artifacts=[first, second])
        host = _artifact_host(tmp_path)

        await asyncio.gather(
            host.mark_artifacts_viewed(
                ArtifactsMarkViewedParams(
                    session_id=SESSION_ID, artifact_ids=[first.id]
                )
            ),
            host.mark_artifacts_viewed(
                ArtifactsMarkViewedParams(
                    session_id=SESSION_ID, artifact_ids=[second.id]
                )
            ),
        )

        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        viewed = {a["id"]: a["viewed_at"] for a in on_disk["artifacts"]}
        assert viewed[first.id] is not None
        assert viewed[second.id] is not None

    @pytest.mark.asyncio
    async def test_a_listed_session_is_kept_fresh_and_announced(
        self, tmp_path: Path, github: _GitHub
    ) -> None:
        # The sidebar lists the sessions it shows, open or not: their PRs
        # refresh, and the reads reach the catalogue channel's subscription.
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir, artifacts=[_github_artifact()])
        before = (session_dir / "meta.json").read_text(encoding="utf-8")
        github.snapshot = _snapshot(state="merged")
        host = _artifact_host(tmp_path, pr_monitoring=True)
        updates: asyncio.Queue[ArtifactUpdatedParams] = asyncio.Queue()
        unsubscribe = host.subscribe_artifacts(
            lambda method, params: updates.put_nowait(params)
        )
        try:
            await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
            update = await asyncio.wait_for(updates.get(), timeout=5)
        finally:
            unsubscribe()
            await _close_cache()

        assert update.session_id == SESSION_ID
        assert update.artifact.details is not None
        assert update.artifact.details.state == "merged"
        listed = await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
        assert listed.artifacts[0].details is not None
        assert listed.artifacts[0].details.state == "merged"
        # The read is the cache's, not the session's: meta.json is untouched.
        assert (session_dir / "meta.json").read_text(encoding="utf-8") == before

    @pytest.mark.asyncio
    async def test_an_announcement_carries_the_view_marked_since_the_listing(
        self, tmp_path: Path, github: _GitHub
    ) -> None:
        # The client replaces its artifact with the announced one: an old
        # copy would show the PR as unread again.
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        artifact = _github_artifact()
        _write_metadata(session_dir, artifacts=[artifact])
        host = _artifact_host(tmp_path, pr_monitoring=True)
        updates: asyncio.Queue[ArtifactUpdatedParams] = asyncio.Queue()
        unsubscribe = host.subscribe_artifacts(
            lambda method, params: updates.put_nowait(params)
        )
        try:
            await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
            await host.mark_artifacts_viewed(
                ArtifactsMarkViewedParams(
                    session_id=SESSION_ID, artifact_ids=[artifact.id]
                )
            )
            # The next background refresh, not a new listing.
            github.snapshot = _snapshot(state="merged")
            from vibe.app_server._pull_request_cache import pull_request_cache

            pull_request_cache().refresh([artifact.external_key], full=True)
            update = await asyncio.wait_for(updates.get(), timeout=5)
        finally:
            unsubscribe()
            await _close_cache()

        assert update.artifact.viewed_at is not None
        assert update.artifact.details is not None
        assert update.artifact.details.state == "merged"

    @pytest.mark.asyncio
    async def test_nothing_is_read_with_pr_monitoring_off(
        self, tmp_path: Path, github: _GitHub
    ) -> None:
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir, artifacts=[_github_artifact()])
        github.snapshot = _snapshot(state="merged")
        host = _artifact_host(tmp_path, pr_monitoring=False)
        unsubscribe = host.subscribe_artifacts(lambda method, params: None)

        await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
        await host.sync_artifacts(ArtifactsSyncParams(session_id=SESSION_ID))
        unsubscribe()
        await _close_cache()

        assert github.reads == 0

    @pytest.mark.asyncio
    async def test_turning_monitoring_off_drops_every_listed_session_watch(
        self, tmp_path: Path, github: _GitHub
    ) -> None:
        """The catalogue channel's watches end at the next listing once
        monitoring is off, for every session it listed, instead of polling
        until the client unsubscribes.
        """
        from vibe.app_server._pull_request_cache import pull_request_cache

        other_session_id = "01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a5f"
        first = _github_artifact()
        second = first.model_copy(
            update={
                "id": "01929a3b-7c4d-7e5f-8a9b-0c1d2e3f4a60",
                "session_id": other_session_id,
                "external_key": "github.com/mistralai/vibe/pull/124",
            }
        )
        for session_id, artifact in ((SESSION_ID, first), (other_session_id, second)):
            session_dir = tmp_path / "unified" / session_id
            session_dir.mkdir(parents=True)
            _write_metadata(session_dir, artifacts=[artifact], session_id=session_id)
        monitoring = {"on": True}
        host = _artifact_host(tmp_path, pr_monitoring=lambda: monitoring["on"])
        unsubscribe = host.subscribe_artifacts(lambda method, params: None)
        try:
            await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
            await host.list_artifacts(ArtifactsListParams(session_id=other_session_id))
            assert {first.external_key, second.external_key} <= (
                pull_request_cache()._watched_keys()
            )

            monitoring["on"] = False
            await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))

            watched = pull_request_cache()._watched_keys()
            assert first.external_key not in watched
            assert second.external_key not in watched
        finally:
            unsubscribe()
            await _close_cache()

    @pytest.mark.asyncio
    async def test_attached_session_delegates_to_the_live_service(
        self, tmp_path: Path
    ) -> None:
        """A session attached on this channel answers from its live service,
        so markViewed announces the update on the session's event stream.
        """
        session_dir = tmp_path / "sessions" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir)
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        emitted: list[str] = []

        async def emit_updated(updated: SessionArtifact) -> None:
            emitted.append(updated.id)

        handler = ArtifactRequestHandler(
            service, _require_session, emit_updated=emit_updated
        )
        adapter = Mock()
        adapter._closed = False
        adapter._artifact_handler = lambda: handler
        host = _artifact_host(tmp_path)
        host._adapters[SESSION_ID] = cast(Any, adapter)

        response = await host.list_artifacts(ArtifactsListParams(session_id=SESSION_ID))
        assert len(response.artifacts) == 1

        (artifact,) = service.list_artifacts()
        await host.mark_artifacts_viewed(
            ArtifactsMarkViewedParams(session_id=SESSION_ID, artifact_ids=[artifact.id])
        )
        assert emitted == [artifact.id]
        assert service.list_artifacts()[0].viewed_at is not None

    @pytest.mark.asyncio
    async def test_sync_of_an_attached_session_refreshes_its_pull_requests(
        self, tmp_path: Path
    ) -> None:
        # The attached session announces the reads on its own event stream.
        adapter = Mock()
        adapter._closed = False
        host = _artifact_host(tmp_path, pr_monitoring=True)
        host._adapters[SESSION_ID] = cast(Any, adapter)

        await host.sync_artifacts(ArtifactsSyncParams(session_id=SESSION_ID))

        # A client asked: the cheap check first, not a full read.
        adapter.refresh_pull_requests.assert_called_once_with(full=False)


# ---------------------------------------------------------------------------
# Telemetry: vibe.artifact_* emission.
# ---------------------------------------------------------------------------


def _telemetry_service(session_dir: Path) -> SessionArtifactService:
    """A service wired to a real SessionTelemetry over a mocked transport.

    The autouse ``telemetry_events`` fixture patches
    ``TelemetryClient.send_telemetry_event``, so events land in that list.
    """
    client = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    return SessionArtifactService(
        session_id=SESSION_ID,
        session_dir=session_dir,
        telemetry=SessionTelemetry(client, model="m1"),
    )


def _artifact_events(
    telemetry_events: list[dict[str, Any]], event_name: str
) -> list[dict[str, Any]]:
    return [e for e in telemetry_events if e["event_name"] == event_name]


class TestServiceTelemetry:
    """``vibe.artifact_*`` emission from ``SessionArtifactService``."""

    @pytest.mark.asyncio
    async def test_detected_and_created_emitted(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.cli_binary_source",
            lambda _name: "system",
        )
        service = _telemetry_service(session_dir)

        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed", message_id="msg-1"
        )

        (detected,) = _artifact_events(telemetry_events, "vibe.artifact_detected")
        props = detected["properties"]
        assert props["provider"] == "github"
        assert props["cli_source"] == "system"
        assert props["command_matched"] is True
        assert props["message_id"] == "msg-1"

        (created,) = _artifact_events(telemetry_events, "vibe.artifact_created")
        props = created["properties"]
        assert props["provider"] == "github"
        assert props["is_draft"] is False
        assert props["host_type"] == "github_com"
        assert props["repo_id_resolved"] is False
        # The provider read fails in these tests: no title yet.
        assert props["has_title"] is False
        assert props["message_id"] == "msg-1"
        assert props["source"] == "detected"

    @pytest.mark.asyncio
    async def test_detected_without_created_when_no_url(
        self, session_dir: Path, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """Detection fires before URL extraction — a miss still counts."""
        service = _telemetry_service(session_dir)

        artifact = await service.on_shell_effect(
            GH_COMMAND, "no url here", "", "completed"
        )
        assert artifact == []
        (detected,) = _artifact_events(telemetry_events, "vibe.artifact_detected")
        assert detected["properties"]["provider"] == "github"
        assert _artifact_events(telemetry_events, "vibe.artifact_created") == []

    @pytest.mark.asyncio
    async def test_no_events_for_non_pr_command(
        self, session_dir: Path, telemetry_events: list[dict[str, Any]]
    ) -> None:
        service = _telemetry_service(session_dir)

        await service.on_shell_effect("ls -la", "file.txt\n", "", "completed")

        assert telemetry_events == []

    @pytest.mark.asyncio
    async def test_no_telemetry_surface_emits_nothing(
        self, service: SessionArtifactService, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """The service built without telemetry (unit tests, other callers)."""
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        assert telemetry_events == []

    @pytest.mark.asyncio
    async def test_duplicate_emits_detected_but_not_created(
        self, session_dir: Path, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """detected counts interceptions, created counts artifacts — the gap
        is the duplicate rate.
        """
        service = _telemetry_service(session_dir)

        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")
        await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        assert len(_artifact_events(telemetry_events, "vibe.artifact_detected")) == 2
        assert len(_artifact_events(telemetry_events, "vibe.artifact_created")) == 1

    @pytest.mark.asyncio
    async def test_mark_viewed_emitted(
        self, session_dir: Path, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """vibe.artifact_mark_viewed fires per markViewed call, counting the
        requested ids and how many were already viewed.
        """
        service = _telemetry_service(session_dir)
        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )

        await service.mark_viewed([artifact.id, "unknown-id"])
        (viewed,) = _artifact_events(telemetry_events, "vibe.artifact_mark_viewed")
        props = viewed["properties"]
        assert props["nb_artifacts"] == 2
        assert props["nb_already_viewed"] == 0

        await service.mark_viewed([artifact.id])
        (_, again) = _artifact_events(telemetry_events, "vibe.artifact_mark_viewed")
        props = again["properties"]
        assert props["nb_artifacts"] == 1
        assert props["nb_already_viewed"] == 1

    @pytest.mark.asyncio
    async def test_created_payload_gitlab_draft_enterprise_with_repo_id(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=4242),
        )
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.cli_binary_source",
            lambda _name: "bundled",
        )
        service = _telemetry_service(session_dir)

        [artifact] = await service.on_shell_effect(
            "glab mr create --draft -t 'Add feature'",
            "https://gitlab.mycompany.com/team/project/-/merge_requests/7\n",
            "",
            "completed",
        )

        (detected,) = _artifact_events(telemetry_events, "vibe.artifact_detected")
        assert detected["properties"]["provider"] == "gitlab"
        assert detected["properties"]["cli_source"] == "bundled"
        (created,) = _artifact_events(telemetry_events, "vibe.artifact_created")
        props = created["properties"]
        assert props["provider"] == "gitlab"
        assert props["is_draft"] is True
        assert props["host_type"] == "enterprise"
        assert props["repo_id_resolved"] is True
        # The provider read fails in these tests: no title yet.
        assert props["has_title"] is False

    @pytest.mark.asyncio
    async def test_created_payload_gitlab_repo_id_unresolved(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(return_value=None),
        )
        service = _telemetry_service(session_dir)

        await service.on_shell_effect(GLAB_COMMAND, GLAB_STDOUT, "", "completed")

        (created,) = _artifact_events(telemetry_events, "vibe.artifact_created")
        props = created["properties"]
        assert props["host_type"] == "gitlab_com"
        assert props["repo_id_resolved"] is False
        assert props["is_draft"] is False

    @pytest.mark.asyncio
    async def test_detection_failed_command_parse(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(_command: str) -> None:
            raise RuntimeError("parse exploded")

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.is_pr_creation_command", boom
        )
        service = _telemetry_service(session_dir)

        with pytest.raises(RuntimeError, match="parse exploded"):
            await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        assert _artifact_events(telemetry_events, "vibe.artifact_detected") == []
        (failed,) = _artifact_events(telemetry_events, "vibe.artifact_detection_failed")
        props = failed["properties"]
        assert props["error_type"] == "RuntimeError"
        assert props["provider"] == "unknown"
        assert props["stage"] == "command_parse"

    @pytest.mark.asyncio
    async def test_detection_failed_output_extract(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(
            _stdout: str,
            _stderr: str,
            provider: str | None = None,
            limit: int | None = None,
            from_start: bool = False,
        ) -> None:
            raise ValueError("output exploded")

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.extract_prs_from_output", boom
        )
        service = _telemetry_service(session_dir)

        with pytest.raises(ValueError, match="output exploded"):
            await service.on_shell_effect(GH_COMMAND, GH_STDOUT, "", "completed")

        # The command matched, so detection fired before the extraction failed.
        assert len(_artifact_events(telemetry_events, "vibe.artifact_detected")) == 1
        (failed,) = _artifact_events(telemetry_events, "vibe.artifact_detection_failed")
        props = failed["properties"]
        assert props["error_type"] == "ValueError"
        assert props["provider"] == "github"
        assert props["stage"] == "output_extract"

    @pytest.mark.asyncio
    async def test_detection_failed_repo_id_resolve(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
            AsyncMock(side_effect=TimeoutError("glab timed out")),
        )
        service = _telemetry_service(session_dir)

        with pytest.raises(TimeoutError, match="glab timed out"):
            await service.on_shell_effect(GLAB_COMMAND, GLAB_STDOUT, "", "completed")

        (failed,) = _artifact_events(telemetry_events, "vibe.artifact_detection_failed")
        props = failed["properties"]
        assert props["error_type"] == "TimeoutError"
        assert props["provider"] == "gitlab"
        assert props["stage"] == "repo_id_resolve"

    @pytest.mark.asyncio
    async def test_persist_failure_still_creates_and_reports(
        self, session_dir: Path, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """B9: a corrupted meta.json → artifact kept, created emitted, failure
        reported at the persist stage (and the error does not propagate).
        """
        (session_dir / "meta.json").write_text("not json {{{", encoding="utf-8")
        service = _telemetry_service(session_dir)

        [artifact] = await service.on_shell_effect(
            GH_COMMAND, GH_STDOUT, "", "completed"
        )
        assert len(service.list_artifacts()) == 1
        assert len(_artifact_events(telemetry_events, "vibe.artifact_created")) == 1
        (failed,) = _artifact_events(telemetry_events, "vibe.artifact_detection_failed")
        props = failed["properties"]
        assert props["error_type"] == "JSONDecodeError"
        assert props["provider"] == "github"
        assert props["stage"] == "persist"


class TestAdapterTelemetry:
    """The adapter wires its SessionTelemetry into the artifact service."""

    @pytest.mark.asyncio
    async def test_drain_emits_detected_and_created(
        self, tmp_path: Path, telemetry_events: list[dict[str, Any]]
    ) -> None:
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(tmp_path, services=services)
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry("e-1", GH_COMMAND, stdout=GH_STDOUT)
            )
        )

        await adapter._drain_pending_shell_effects()

        assert len(_artifact_events(telemetry_events, "vibe.artifact_detected")) == 1
        assert len(_artifact_events(telemetry_events, "vibe.artifact_created")) == 1
        assert (
            _artifact_events(telemetry_events, "vibe.artifact_detection_failed") == []
        )


# ---------------------------------------------------------------------------
# Phase 2: discovery of pre-existing PRs/MRs at session load.
# ---------------------------------------------------------------------------


def _discovered_github(
    number: int = 123, *, title: str = "Fix login", draft: bool = False
) -> DiscoveredPullRequest:
    return DiscoveredPullRequest(
        provider="github",
        number=number,
        title=title,
        url=f"https://github.com/mistralai/vibe/pull/{number}",
        source_branch=f"branch-{number}",
        target_branch="main",
        state="draft" if draft else "open",
        author="pierre",
        repo_host="github.com",
        repo_owner="mistralai",
        repo_name="vibe",
    )


def _discovered_gitlab(
    iid: int = 7, *, title: str = "Add feature"
) -> DiscoveredPullRequest:
    return DiscoveredPullRequest(
        provider="gitlab",
        number=iid,
        title=title,
        url=f"https://gitlab.com/team/project/-/merge_requests/{iid}",
        source_branch=f"feature-{iid}",
        target_branch="main",
        state="open",
        author="pierre",
        repo_host="gitlab.com",
        repo_owner="team/project",
        repo_name="project",
    )


def _patch_repo_id(monkeypatch: pytest.MonkeyPatch, repo_id: int | None) -> None:
    """Replace the glab api repo_id resolver inside the artifact service."""
    monkeypatch.setattr(
        "vibe.app_server._session_artifacts.resolve_gitlab_repo_id",
        AsyncMock(return_value=repo_id),
    )


def _patch_discovery(monkeypatch: pytest.MonkeyPatch, result: object):
    """Replace the forge discovery call inside the artifact service module.

    The GitLab repo_id resolver is neutralized too (no glab api subprocess in
    tests); tests that exercise repo_id resolution override it afterwards
    with ``_patch_repo_id``.
    """
    _patch_repo_id(monkeypatch, None)
    if isinstance(result, BaseException):
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests",
            MagicMock(side_effect=result),
        )
    else:
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests",
            MagicMock(return_value=result),
        )


class TestDiscoverAndCreate:
    """``SessionArtifactService.discover_and_create`` (Phase 2)."""

    @pytest.mark.asyncio
    async def test_creates_artifacts_from_discovery(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_discovery(
            monkeypatch, [_discovered_github(123), _discovered_github(124, draft=True)]
        )

        created = await service.discover_and_create(Path("/repo"))

        assert len(created) == 2
        first, second = created
        assert isinstance(first, GitHubPullRequestArtifact)
        assert first.external_key == "github.com/mistralai/vibe/pull/123"
        assert first.external_url == "https://github.com/mistralai/vibe/pull/123"
        assert first.display_title == "Fix login"
        assert first.details is not None
        assert first.details.repo_host == "github.com"
        assert first.details.repo_owner == "mistralai"
        assert first.details.repo_name == "vibe"
        assert first.details.pull_request_number == 123
        assert first.details.state == "open"
        assert first.details.source_branch == "branch-123"
        assert first.details.target_branch == "main"
        assert first.details.author == "pierre"
        assert second.details is not None
        assert second.details.state == "draft"
        assert len(service.list_artifacts()) == 2

    @pytest.mark.asyncio
    async def test_gitlab_discovered_mr(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_discovery(monkeypatch, [_discovered_gitlab(7)])

        (created,) = await service.discover_and_create(Path("/repo"))

        assert isinstance(created, GitLabMergeRequestArtifact)
        assert created.provider == "gitlab"
        # The repo_id resolver returned None — the external key falls back to
        # the project path, exactly like Phase 1 without glab.
        assert created.external_key == "gitlab.com/team/project/-/merge_requests/7"
        assert created.details is not None
        assert created.details.project_path == "team/project"
        assert created.details.merge_request_iid == 7
        assert created.details.repo_id is None

    @pytest.mark.asyncio
    async def test_gitlab_repo_id_resolved_for_key_parity(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        """When glab api resolves the numeric repo_id, the discovered MR uses
        the same external key as a Phase 1-detected one — the keys must match
        for cross-phase dedup.
        """
        _patch_discovery(monkeypatch, [_discovered_gitlab(7)])
        _patch_repo_id(monkeypatch, 42)

        (created,) = await service.discover_and_create(Path("/repo"))

        assert isinstance(created, GitLabMergeRequestArtifact)
        assert created.external_key == "gitlab.com/42/merge_requests/7"
        assert created.details is not None
        assert created.details.repo_id == 42

    @pytest.mark.asyncio
    async def test_cross_phase_dedup_when_creation_races_discovery(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Phase 1 records the MR while discovery is in flight; discovery
        resolves the same repo_id → same key → no duplicate artifact.
        """
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        phase1 = _gitlab_artifact(repo_id=42)

        def racing_discovery(
            _path: Path, **_kwargs: Any
        ) -> list[DiscoveredPullRequest]:
            service._artifacts[phase1.external_key] = phase1
            return [_discovered_gitlab(7)]

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests",
            racing_discovery,
        )
        _patch_repo_id(monkeypatch, 42)

        created = await service.discover_and_create(Path("/repo"))

        assert created == []
        assert [a.external_key for a in service.list_artifacts()] == [
            "gitlab.com/42/merge_requests/7"
        ]

    @pytest.mark.asyncio
    async def test_cross_phase_dedup_by_url_when_repo_id_differs(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Phase 1 could not resolve a repo_id (path-based key) while
        discovery did (id-based key): the keys differ, so the secondary
        external_url dedup catches the duplicate.
        """
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        phase1 = _gitlab_artifact(repo_id=None)

        def racing_discovery(
            _path: Path, **_kwargs: Any
        ) -> list[DiscoveredPullRequest]:
            service._artifacts[phase1.external_key] = phase1
            return [_discovered_gitlab(7)]

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests",
            racing_discovery,
        )
        _patch_repo_id(monkeypatch, 42)

        created = await service.discover_and_create(Path("/repo"))

        assert created == []
        assert [a.external_key for a in service.list_artifacts()] == [
            "gitlab.com/team/project/-/merge_requests/7"
        ]

    @pytest.mark.asyncio
    async def test_phase1_skips_mr_already_discovered(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        """The other direction of the race: discovery recorded the MR first
        (path-based key), Phase 1 later detects the same MR with a resolved
        repo_id — the external_url dedup keeps it to one artifact.
        """
        _patch_discovery(monkeypatch, [_discovered_gitlab(7)])
        await service.discover_and_create(Path("/repo"))
        assert [a.external_key for a in service.list_artifacts()] == [
            "gitlab.com/team/project/-/merge_requests/7"
        ]

        _patch_repo_id(monkeypatch, 42)
        artifact = await service.on_shell_effect(
            GLAB_COMMAND, GLAB_STDOUT, "", "completed"
        )

        assert artifact == []
        assert [a.external_key for a in service.list_artifacts()] == [
            "gitlab.com/team/project/-/merge_requests/7"
        ]

    @pytest.mark.asyncio
    async def test_persists_to_meta_json(
        self, service: SessionArtifactService, session_dir: Path, monkeypatch
    ):
        _patch_discovery(monkeypatch, [_discovered_github(123)])

        await service.discover_and_create(Path("/repo"))

        metadata = SessionLoader.load_metadata(session_dir)
        assert len(metadata.artifacts) == 1
        restored = metadata.artifacts[0]
        assert isinstance(restored, GitHubPullRequestArtifact)
        assert restored.external_key == "github.com/mistralai/vibe/pull/123"
        assert restored.display_title == "Fix login"

    @pytest.mark.asyncio
    async def test_skips_branches_the_session_already_has_a_pr_for(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _write_metadata(
            session_dir, artifacts=[_github_artifact(source_branch="branch-123")]
        )
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        service.load(SessionLoader.load_metadata(session_dir))
        discover = MagicMock(return_value=[])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )

        await service.discover_and_create(Path("/repo"))

        discover.assert_called_once_with(
            Path("/repo"), skip_heads={("github.com", "mistralai/vibe", "branch-123")}
        )

    @pytest.mark.parametrize("state", ["merged", "closed"])
    @pytest.mark.asyncio
    async def test_finds_a_new_pr_on_a_branch_whose_pr_is_finished(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch, state: str
    ):
        """A branch whose PR was merged or closed can get a new one, e.g. after
        a squash-merge the agent keeps working on the branch.
        """
        finished = _github_artifact(source_branch="branch-123")
        assert finished.details is not None
        finished = finished.model_copy(
            update={"details": finished.details.model_copy(update={"state": state})}
        )
        _write_metadata(session_dir, artifacts=[finished])
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        service.load(SessionLoader.load_metadata(session_dir))
        discover = MagicMock(return_value=[_discovered_github(456)])
        _patch_discovery(monkeypatch, [])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )

        created = await service.discover_and_create(Path("/repo"))

        discover.assert_called_once_with(Path("/repo"), skip_heads=set())
        assert [a.external_key for a in created] == [
            "github.com/mistralai/vibe/pull/456"
        ]
        assert [a.external_key for a in service.list_artifacts()] == [
            "github.com/mistralai/vibe/pull/123",
            "github.com/mistralai/vibe/pull/456",
        ]

    @pytest.mark.asyncio
    async def test_finds_a_new_pr_when_the_cache_read_the_old_one_merged(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The stored artifact keeps the state it was created with; a merge
        is only in the pull request cache's read.
        """
        from vibe.app_server import _pull_request_cache as cache_module

        stored = _github_artifact(source_branch="branch-123")
        _write_metadata(session_dir, artifacts=[stored])
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        service.load(SessionLoader.load_metadata(session_dir))
        now = cache_module._now_iso()
        cache_module._write_entry(
            cache_module.pull_request_cache()._directory,
            cache_module.CachedPullRequest(
                external_key=stored.external_key,
                snapshot=_snapshot(state="merged"),
                read_at=now,
                full_read_at=now,
            ),
        )
        _patch_discovery(monkeypatch, [])
        discover = MagicMock(return_value=[])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )

        await service.discover_and_create(Path("/repo"))

        discover.assert_called_once_with(Path("/repo"), skip_heads=set())

    @pytest.mark.asyncio
    async def test_adds_the_new_branch_pr_and_keeps_the_old_one(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A session moved to another branch keeps the PRs it had and gains
        the new branch's PR.
        """
        _write_metadata(
            session_dir, artifacts=[_github_artifact(source_branch="release/x")]
        )
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        service.load(SessionLoader.load_metadata(session_dir))
        _patch_discovery(monkeypatch, [_discovered_github(456)])

        created = await service.discover_and_create(Path("/worktree"))

        assert [a.external_key for a in created] == [
            "github.com/mistralai/vibe/pull/456"
        ]
        assert [a.external_key for a in service.list_artifacts()] == [
            "github.com/mistralai/vibe/pull/123",
            "github.com/mistralai/vibe/pull/456",
        ]

    @pytest.mark.asyncio
    async def test_dedupes_within_discovery_result(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        """The same PR listed twice (or created by Phase 1 while discovery
        was in flight) yields one artifact — dedup is on external_key.
        """
        _patch_discovery(
            monkeypatch,
            [_discovered_github(123), _discovered_github(123), _discovered_github(124)],
        )

        created = await service.discover_and_create(Path("/repo"))

        assert [a.external_key for a in created] == [
            "github.com/mistralai/vibe/pull/123",
            "github.com/mistralai/vibe/pull/124",
        ]
        assert len(service.list_artifacts()) == 2

    @pytest.mark.asyncio
    async def test_forge_error_returns_empty(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        """No CLI / no auth / no remote: discovery is skipped, never raised."""
        _patch_discovery(monkeypatch, ForgeError("gh CLI not found"))

        assert await service.discover_and_create(Path("/repo")) == []
        assert service.list_artifacts() == []

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_empty(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_discovery(monkeypatch, RuntimeError("boom"))

        assert await service.discover_and_create(Path("/repo")) == []
        assert service.list_artifacts() == []

    @pytest.mark.asyncio
    async def test_no_open_prs_returns_empty(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        _patch_discovery(monkeypatch, [])

        assert await service.discover_and_create(Path("/repo")) == []

    @pytest.mark.asyncio
    async def test_corrupted_meta_json_keeps_in_memory_artifacts(
        self, session_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """B9 applies to discovery too: persist failure never loses artifacts."""
        (session_dir / "meta.json").write_text("not json {{{", encoding="utf-8")
        service = SessionArtifactService(session_id=SESSION_ID, session_dir=session_dir)
        _patch_discovery(monkeypatch, [_discovered_github(123)])

        created = await service.discover_and_create(Path("/repo"))

        assert len(created) == 1
        assert len(service.list_artifacts()) == 1

    @pytest.mark.asyncio
    async def test_telemetry_created_with_discovered_source_emitted(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Discovery emits one ``vibe.artifact_created`` per artifact with
        ``source=discovered`` (no separate ``vibe.artifact_discovered`` event).
        """
        _patch_discovery(monkeypatch, [_discovered_github(123), _discovered_gitlab(7)])
        service = _telemetry_service(session_dir)

        await service.discover_and_create(Path("/repo"))

        created = _artifact_events(telemetry_events, "vibe.artifact_created")
        assert len(created) == 2
        by_provider = {e["properties"]["provider"]: e["properties"] for e in created}
        assert by_provider["github"]["source"] == "discovered"
        assert by_provider["github"]["host_type"] == "github_com"
        assert by_provider["github"]["is_draft"] is False
        assert by_provider["github"]["has_title"] is True
        assert by_provider["github"]["message_id"] is None
        assert by_provider["gitlab"]["source"] == "discovered"
        assert by_provider["gitlab"]["host_type"] == "gitlab_com"
        assert by_provider["gitlab"]["repo_id_resolved"] is False
        assert _artifact_events(telemetry_events, "vibe.artifact_discovered") == []

    @pytest.mark.asyncio
    async def test_no_telemetry_when_nothing_discovered(
        self,
        session_dir: Path,
        telemetry_events: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_discovery(monkeypatch, [])
        service = _telemetry_service(session_dir)

        await service.discover_and_create(Path("/repo"))

        assert telemetry_events == []


class TestSchedulePrDiscovery:
    """The adapter gating: flag, sandbox, cwd — before any task is spawned."""

    @pytest.mark.asyncio
    async def test_flag_off_spawns_nothing(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path, pr_monitoring=False, cwd="/repo")
        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is None

    @pytest.mark.asyncio
    async def test_sandboxed_session_spawns_nothing(self, tmp_path: Path) -> None:
        """Local only: a sandboxed session's checkout is not on this disk."""
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        # A real sandboxed derivation carries a sandbox-backed workspace; the
        # gate only reads ``sandbox``, so a minimal double is enough here.
        adapter._adapter_config = cast(Any, SimpleNamespace(sandbox=object()))
        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is None

    @pytest.mark.asyncio
    async def test_missing_cwd_spawns_nothing(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd=None)
        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is None

    @pytest.mark.asyncio
    async def test_enabled_spawns_background_task(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

    @pytest.mark.asyncio
    async def test_second_schedule_is_a_noop(self, tmp_path: Path) -> None:
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        adapter.schedule_pr_discovery()
        first = adapter._pr_discovery_task
        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is first
        await first

    @pytest.mark.asyncio
    async def test_closed_adapter_spawns_nothing(self, tmp_path: Path) -> None:
        """An after_response racing shutdown must not spawn an orphan task."""
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        adapter._closed = True
        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is None


class TestAdapterPrDiscovery:
    """End to end: background discovery creates artifacts and notifies."""

    @pytest.mark.asyncio
    async def test_discovery_creates_and_notifies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_discovery(monkeypatch, [_discovered_github(123), _discovered_gitlab(7)])
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )

        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

        artifacts = adapter._artifact_service.list_artifacts()
        assert [a.external_key for a in artifacts] == [
            "github.com/mistralai/vibe/pull/123",
            "gitlab.com/team/project/-/merge_requests/7",
        ]
        assert [method for method, _ in services.notifications] == [
            "artifacts/created",
            "artifacts/created",
        ]
        _, params = services.notifications[0]
        assert isinstance(params, ArtifactCreatedParams)
        assert params.artifact.external_key == "github.com/mistralai/vibe/pull/123"
        assert params.artifact.display_title == "Fix login"

    @pytest.mark.asyncio
    async def test_discovery_covers_every_worktree_the_session_entered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Prepare*: A session now in worktree ``current`` that entered ``first``
        and ``broken`` before, and recorded a worktree since deleted.
        *Do*: Run PR discovery.
        *Assert*: The current worktree is queried first, then the earlier live
        ones; the deleted one is not; a failing query does not stop the rest,
        and a PR found on an earlier worktree becomes an artifact.
        """
        current, first, broken, gone = (
            tmp_path / name for name in ("current", "first", "broken", "gone")
        )
        for path in (current, first, broken):
            path.mkdir()
        queried: list[Path] = []

        def discover(path: Path, **_kwargs: Any) -> list[DiscoveredPullRequest]:
            queried.append(path)
            if path == broken:
                raise RuntimeError("gh crashed")
            return [_discovered_github(1)] if path == first else []

        _patch_discovery(monkeypatch, [])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd=str(current))
        for path in (broken, first, current, gone):
            await adapter._artifact_service.record_worktree(
                path=path, branch=f"vibe/{path.name}", repo_root=tmp_path / "repo"
            )

        adapter.schedule_pr_discovery()
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

        assert queried == [current, broken, first]
        assert [a.external_key for a in adapter._artifact_service.list_artifacts()] == [
            "github.com/mistralai/vibe/pull/1"
        ]

    @pytest.mark.asyncio
    async def test_turn_end_discovery_queries_the_current_worktree_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Prepare*: A session in ``current`` that entered ``first`` before.
        *Do*: Run the discovery a turn that ran commands schedules.
        *Assert*: Only the current worktree, where the commands ran, is queried.
        """
        current, first = tmp_path / "current", tmp_path / "first"
        current.mkdir()
        first.mkdir()
        queried: list[Path] = []

        def discover(path: Path, **_kwargs: Any) -> list[DiscoveredPullRequest]:
            queried.append(path)
            return []

        _patch_discovery(monkeypatch, [])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd=str(current))
        for path in (first, current):
            await adapter._artifact_service.record_worktree(
                path=path, branch=f"vibe/{path.name}", repo_root=tmp_path / "repo"
            )

        adapter._run_turn_end_pr_discovery()
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

        assert queried == [current]

    @pytest.mark.asyncio
    async def test_a_full_pass_asked_during_a_turn_end_pass_covers_every_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """*Prepare*: A session in ``current`` that entered ``first`` before, and a
        turn-end discovery pass held while it queries ``current``.
        *Do*: Ask for a full pass (as a move does), then for another turn-end
        pass, then release the held one.
        *Assert*: The rerun covers both worktrees.
        """
        current, first = tmp_path / "current", tmp_path / "first"
        current.mkdir()
        first.mkdir()
        release = __import__("threading").Event()
        queried: list[Path] = []

        def discover(path: Path, **_kwargs: Any) -> list[DiscoveredPullRequest]:
            queried.append(path)
            release.wait(timeout=10)
            return []

        _patch_discovery(monkeypatch, [])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd=str(current))
        for path in (first, current):
            await adapter._artifact_service.record_worktree(
                path=path, branch=f"vibe/{path.name}", repo_root=tmp_path / "repo"
            )

        adapter._run_turn_end_pr_discovery()
        task = adapter._pr_discovery_task
        assert task is not None

        async def started() -> None:
            while not queried:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(started(), timeout=5)
        adapter.schedule_pr_discovery()
        adapter._run_turn_end_pr_discovery()
        release.set()
        await task

        assert queried == [current, current, first]

    @pytest.mark.asyncio
    async def test_discovery_is_non_blocking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The UI renders the conversation first: scheduling returns before
        any artifact exists, and the artifacts arrive when the task settles.
        """
        release = __import__("threading").Event()

        def slow_discovery(_path: Path, **_kwargs: Any) -> list[DiscoveredPullRequest]:
            release.wait(timeout=10)
            return [_discovered_github(123)]

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", slow_discovery
        )
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )

        adapter.schedule_pr_discovery()
        # The scheduling call returned synchronously; discovery is in flight.
        assert adapter._artifact_service.list_artifacts() == []
        assert services.notifications == []

        release.set()
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task
        assert len(adapter._artifact_service.list_artifacts()) == 1
        assert [method for method, _ in services.notifications] == ["artifacts/created"]

    @pytest.mark.asyncio
    async def test_discovery_syncs_adapter_metadata_and_meta_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir)
        _patch_discovery(monkeypatch, [_discovered_github(123)])
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )

        await adapter._discover_pull_requests()

        assert adapter._metadata is not None
        assert [a.external_key for a in adapter._metadata.artifacts] == [
            "github.com/mistralai/vibe/pull/123"
        ]
        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert [a["external_key"] for a in on_disk["artifacts"]] == [
            "github.com/mistralai/vibe/pull/123"
        ]

    @pytest.mark.asyncio
    async def test_discovery_waits_for_the_worktree_the_session_starts_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a session started with a worktree opens in the origin
        checkout, whose branch is unrelated, and moves once the worktree is
        ready. Discovering at open recorded the origin branch's PR.
        """
        discover = MagicMock(return_value=[_discovered_github(123)])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/origin")
        moved = asyncio.Event()

        async def setup() -> None:
            await moved.wait()
            adapter._session.cwd = "/worktree"

        adapter.defer_turns(setup())
        adapter.schedule_pr_discovery()
        discovery_during_setup = adapter._pr_discovery_task

        moved.set()
        await adapter._deferred_setup_task
        task = adapter._pr_discovery_task
        assert task is not None
        await task

        assert discovery_during_setup is None

        discover.assert_called_once_with(Path("/worktree"), skip_heads=set())

    @pytest.mark.asyncio
    async def test_abandoned_worktree_setup_discovers_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        discover = MagicMock(return_value=[_discovered_github(123)])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/origin")

        async def setup() -> None:
            raise RuntimeError("checkout failed")

        adapter.defer_turns(setup())
        adapter.schedule_pr_discovery()
        await adapter._deferred_setup_task

        assert adapter._pr_discovery_task is None
        discover.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_abandoned_before_open_discovers_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        discover = MagicMock(return_value=[_discovered_github(123)])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/origin")

        async def setup() -> None:
            raise RuntimeError("checkout failed")

        adapter.defer_turns(setup())
        await adapter._deferred_setup_task
        adapter.schedule_pr_discovery()

        assert adapter._pr_discovery_task is None
        discover.assert_not_called()

    @pytest.mark.asyncio
    async def test_discovery_does_not_reannounce_persisted_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session_dir = tmp_path / "unified" / SESSION_ID
        session_dir.mkdir(parents=True)
        _write_metadata(session_dir, artifacts=[_github_artifact()])
        _patch_discovery(monkeypatch, [_discovered_github(123)])
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )

        await adapter._discover_pull_requests()

        assert len(adapter._artifact_service.list_artifacts()) == 1
        assert services.notifications == []

    @pytest.mark.asyncio
    async def test_every_discovery_pass_watches_the_session_pull_requests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A pass after a move may add the new checkout's PRs: the cache must
        # watch them too, not only the ones of the load pass.
        _patch_discovery(monkeypatch, [])
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        watches: list[None] = []
        monkeypatch.setattr(
            adapter, "_watch_pull_requests", lambda: watches.append(None)
        )

        await adapter._discover_pull_requests()
        await adapter._discover_pull_requests()

        assert len(watches) == 2

    @pytest.mark.asyncio
    async def test_a_move_during_discovery_discovers_the_new_checkout_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        release = threading.Event()
        seen: list[Path] = []

        def discovery(path: Path, **_kwargs: Any) -> list[DiscoveredPullRequest]:
            seen.append(path)
            release.wait(timeout=10)
            return []

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discovery
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/origin")
        adapter.schedule_pr_discovery()
        task = adapter._pr_discovery_task
        assert task is not None
        await asyncio.sleep(0.05)

        adapter._session.cwd = "/worktree"
        adapter.schedule_pr_discovery()
        release.set()
        await task

        assert seen == [Path("/origin"), Path("/worktree")]

    @pytest.mark.asyncio
    async def test_service_failure_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovery crash never propagates into the session load path."""
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )

        async def boom(_path: Path) -> list:
            raise RuntimeError("discovery exploded")

        monkeypatch.setattr(adapter._artifact_service, "discover_and_create", boom)
        await adapter._discover_pull_requests()

        assert adapter._artifact_service.list_artifacts() == []
        assert services.notifications == []

    @pytest.mark.asyncio
    async def test_closed_session_emits_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Discovery finishing after shutdown must not notify a dead session."""
        _patch_discovery(monkeypatch, [_discovered_github(123)])
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )
        adapter._closed = True

        await adapter._discover_pull_requests()

        assert services.notifications == []

    @pytest.mark.asyncio
    async def test_shutdown_cancels_in_flight_discovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        import threading

        release = threading.Event()

        def blocking_discovery(
            _path: Path, **_kwargs: Any
        ) -> list[DiscoveredPullRequest]:
            release.wait(timeout=10)
            return [_discovered_github(123)]

        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests",
            blocking_discovery,
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        try:
            adapter.schedule_pr_discovery()
            task = adapter._pr_discovery_task
            assert task is not None
            # Give the background task a chance to reach the blocking call.
            await asyncio.sleep(0.05)
            cancelled = adapter._cancel_pr_discovery_task()
            assert cancelled is task
            # Let the event loop settle the cancellation.
            await asyncio.gather(task, return_exceptions=True)
            assert task.cancelled()
        finally:
            release.set()

    @pytest.mark.asyncio
    async def test_promotion_persist_carries_discovered_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression (E2E scenario 1): a fresh session starts ephemeral, so
        discovery runs before meta.json exists and the service's own persist
        no-ops. The promotion-time metadata persist must still write the
        discovered artifacts — the service is the source of truth for the
        artifacts field on every full persist.
        """
        pytest.importorskip("mistralai_vibe_local_harness.vibe")
        from mistralai_vibe_local_harness.session_protocol import (
            IdleSessionStatus,
            PublicSession as HarnessPublicSession,
        )
        from vibe.app_server._unified_harness_backend_adapter import (
            _build_unified_session_metadata,
        )

        _patch_discovery(
            monkeypatch, [_discovered_github(123), _discovered_github(124)]
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        session_dir = tmp_path / "unified" / SESSION_ID
        metadata_before = adapter._metadata
        assert metadata_before is None
        assert not (session_dir / "meta.json").exists()

        # Discovery runs while the session is still ephemeral: the service's
        # own persist no-ops (no meta.json yet), artifacts live in memory.
        await adapter._discover_pull_requests()
        assert len(adapter._artifact_service.list_artifacts()) == 2
        assert not (session_dir / "meta.json").exists()

        # The session is promoted (first turn): the promotion persist must
        # write meta.json WITH the discovered artifacts.
        adapter._session.ephemeral = False
        harness_session = HarnessPublicSession(
            id=SESSION_ID,
            status=IdleSessionStatus(),
            created_at=1_760_000_000_000,
            updated_at=1_760_000_100_000,
            title="Session title",
        )
        metadata = _build_unified_session_metadata(
            harness_session, "/repo", previous=adapter._metadata
        )
        async with adapter._metadata_lock:
            await adapter._store_metadata_locked(metadata)

        on_disk = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
        assert [a["external_key"] for a in on_disk["artifacts"]] == [
            "github.com/mistralai/vibe/pull/123",
            "github.com/mistralai/vibe/pull/124",
        ]
        assert adapter._metadata is not None
        assert [a.external_key for a in adapter._metadata.artifacts] == [
            "github.com/mistralai/vibe/pull/123",
            "github.com/mistralai/vibe/pull/124",
        ]

    @pytest.mark.asyncio
    async def test_notify_failure_does_not_stop_remaining_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One artifact's announcement failing (e.g. the client is gone) must
        not drop the remaining artifacts or kill the discovery task.
        """
        _patch_discovery(
            monkeypatch, [_discovered_github(123), _discovered_github(124)]
        )
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        announced: list[str] = []

        async def flaky_notify(artifact: SessionArtifact) -> None:
            announced.append(artifact.external_key)
            if len(announced) == 1:
                raise RuntimeError("client gone")

        monkeypatch.setattr(adapter, "_emit_artifact_created", flaky_notify)

        await adapter._discover_pull_requests()

        assert announced == [
            "github.com/mistralai/vibe/pull/123",
            "github.com/mistralai/vibe/pull/124",
        ]
        assert len(adapter._artifact_service.list_artifacts()) == 2


def _complete_turn(adapter: Any) -> None:
    from vibe.app_server.events import TurnCompleted
    from vibe.app_server.models import PublicTurn, PublicTurnStatus

    turn = PublicTurn(
        id="turn-1",
        session_id=SESSION_ID,
        status=PublicTurnStatus.COMPLETED,
        started_at=1,
    )
    adapter._interleave_stats(
        [TurnCompleted(turn=turn)], _projection().state, cast(Any, None), updated=False
    )


class TestTurnEndPrDiscovery:
    """A PR created while the session stays open is found when the turn ends,
    however the command that created it was spelled.
    """

    @pytest.mark.asyncio
    async def test_a_pr_created_by_a_script_is_found_when_the_turn_ends(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A command that does not spell out ``gh pr create`` (a script, a
        Makefile target) is not recognised as a creation, and discovery only
        ran at session load.
        """
        _patch_discovery(monkeypatch, [_discovered_github(123)])
        services = FakeSessionBackendServices()
        adapter = _artifact_adapter(
            tmp_path, pr_monitoring=True, cwd="/repo", services=services
        )
        adapter._collect_pending_shell_effects(
            _state_with_entries(
                _shell_effect_entry(
                    "e-1", "./scripts/open-pr.sh --draft", stdout=GH_STDOUT
                )
            )
        )
        await adapter._drain_pending_shell_effects()
        assert adapter._artifact_service.list_artifacts() == []

        _complete_turn(adapter)
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

        assert [a.external_key for a in adapter._artifact_service.list_artifacts()] == [
            "github.com/mistralai/vibe/pull/123"
        ]
        assert "artifacts/created" in [m for m, _ in services.notifications]

    @pytest.mark.asyncio
    async def test_a_read_finding_the_pr_merged_discovers_the_branch_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The turn-end read that finds a squash-merged PR lands after the
        turn-end discovery, which still saw it open: the read itself reruns
        discovery, so a new PR on the branch is found.
        """
        from vibe.app_server import _pull_request_cache as cache_module

        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        service = adapter._artifact_service
        stored = _github_artifact(source_branch="branch-123")
        service._artifacts[stored.external_key] = stored
        _patch_discovery(monkeypatch, [])
        discover = MagicMock(return_value=[_discovered_github(456)])
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.discover_pull_requests", discover
        )
        now = cache_module._now_iso()
        merged = cache_module.CachedPullRequest(
            external_key=stored.external_key,
            snapshot=_snapshot(state="merged"),
            read_at=now,
            full_read_at=now,
        )
        cache_module._write_entry(cache_module.pull_request_cache()._directory, merged)

        adapter._pull_request_read(
            cache_module.Read(SESSION_ID, merged, changed=True, local=True)
        )
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

        discover.assert_called_once_with(Path("/repo"), skip_heads=set())
        assert "github.com/mistralai/vibe/pull/456" in [
            a.external_key for a in service.list_artifacts()
        ]

    @pytest.mark.asyncio
    async def test_a_read_finding_the_pr_still_open_discovers_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vibe.app_server import _pull_request_cache as cache_module

        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        stored = _github_artifact(source_branch="branch-123")
        adapter._artifact_service._artifacts[stored.external_key] = stored
        now = cache_module._now_iso()
        read = cache_module.CachedPullRequest(
            external_key=stored.external_key,
            snapshot=_snapshot(state="open"),
            read_at=now,
            full_read_at=now,
        )

        adapter._pull_request_read(
            cache_module.Read(SESSION_ID, read, changed=True, local=True)
        )

        assert adapter._pr_discovery_task is None

    @pytest.mark.asyncio
    async def test_a_turn_without_shell_commands_discovers_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_discovery(monkeypatch, [_discovered_github(123)])
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")

        _complete_turn(adapter)

        assert adapter._pr_discovery_task is None

    @pytest.mark.asyncio
    async def test_turns_ending_close_together_discover_once_then_after_the_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._unified_harness_backend_adapter"
            "._TURN_END_PR_DISCOVERY_INTERVAL_S",
            0.05,
        )
        _patch_discovery(monkeypatch, [])
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        calls: list[Path] = []

        async def discover(path: Path) -> list[SessionArtifact]:
            calls.append(path)
            return []

        monkeypatch.setattr(adapter._artifact_service, "discover_and_create", discover)

        adapter._collect_pending_shell_effects(
            _state_with_entries(_shell_effect_entry("e-1", "git push"))
        )
        _complete_turn(adapter)
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task

        adapter._collect_pending_shell_effects(
            _state_with_entries(_shell_effect_entry("e-2", "gh pr create"))
        )
        _complete_turn(adapter)
        assert len(calls) == 1

        # The last turn's command is not lost: it runs once the window ends.
        await asyncio.sleep(0.15)
        assert adapter._pr_discovery_task is not None
        await adapter._pr_discovery_task
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_shutdown_cancels_the_pending_turn_end_discovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "vibe.app_server._unified_harness_backend_adapter"
            "._TURN_END_PR_DISCOVERY_INTERVAL_S",
            0.05,
        )
        _patch_discovery(monkeypatch, [])
        adapter = _artifact_adapter(tmp_path, pr_monitoring=True, cwd="/repo")
        for effect_id in ("e-1", "e-2"):
            adapter._collect_pending_shell_effects(
                _state_with_entries(_shell_effect_entry(effect_id, "git push"))
            )
            _complete_turn(adapter)
            if adapter._pr_discovery_task is not None:
                await adapter._pr_discovery_task
        first = adapter._pr_discovery_task

        adapter._cancel_turn_end_pr_discovery()
        await asyncio.sleep(0.15)

        assert adapter._pr_discovery_task is first


def _snapshot(
    *,
    state: str = "open",
    title: str = "Fix login",
    checks: ChecksSummary | None = None,
    review_decision: Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"]
    | None = None,
    merge_status: Literal["unknown", "mergeable", "blocked", "conflicting"]
    | None = None,
    optional_checks_state: Literal["pass", "fail", "pending", "none"] | None = None,
):
    """A provider snapshot matching the default test artifact."""
    from vibe.core.git.forge_sync import PullRequestSnapshot

    return PullRequestSnapshot(
        state=state,  # type: ignore[arg-type]
        title=title,
        source_branch="fix/login",
        target_branch="main",
        author="pierre",
        created_at="2026-10-20T10:00:00+00:00",
        closed_at=None,
        merged_at=None,
        merge_commit_sha=None,
        checks=checks,
        optional_checks_state=optional_checks_state,
        review_decision=review_decision,
        merge_status=merge_status,
    )


async def _close_cache() -> None:
    from vibe.app_server._pull_request_cache import pull_request_cache

    await pull_request_cache().aclose()


def _seed_service_with_artifact(service: SessionArtifactService) -> SessionArtifact:
    """Load the default GitHub artifact into the service (as persisted)."""
    artifact = _github_artifact()
    metadata = _write_metadata(service._session_dir, artifacts=[artifact])
    service.load(metadata)
    return artifact


class TestReopenDetection:
    """`gh pr reopen` / `glab mr reopen` produce no URL: the coordinates
    resolve from the session checkout's remote.
    """

    @pytest.mark.asyncio
    async def test_reopen_resolves_coordinates_from_remote(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        fake_repo = MagicMock()
        fake_repo.status.return_value.repo_url = (
            "https://github.com/mistralai/dashboard.git"
        )
        fake_repo.__enter__ = MagicMock(return_value=fake_repo)
        fake_repo.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.WorktreeRepository",
            MagicMock(open=MagicMock(return_value=fake_repo)),
        )

        [artifact] = await service.on_shell_effect(
            "gh pr reopen 67176",
            "✓ Reopened pull request mistralai/dashboard#67176 (Fix bug)\n",
            "",
            "completed",
            cwd="/repo",
        )
        assert artifact.external_url == (
            "https://github.com/mistralai/dashboard/pull/67176"
        )
        assert artifact.external_key == "github.com/mistralai/dashboard/pull/67176"
        assert artifact.display_title is None
        assert artifact.details is not None
        assert artifact.details.state == "open"

    @pytest.mark.asyncio
    async def test_reopen_number_only_output_resolves_owner_and_repo(
        self, service: SessionArtifactService, monkeypatch: pytest.MonkeyPatch
    ):
        fake_repo = MagicMock()
        fake_repo.status.return_value.repo_url = (
            "https://github.com/mistralai/dashboard.git"
        )
        fake_repo.__enter__ = MagicMock(return_value=fake_repo)
        fake_repo.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(
            "vibe.app_server._session_artifacts.WorktreeRepository",
            MagicMock(open=MagicMock(return_value=fake_repo)),
        )

        [artifact] = await service.on_shell_effect(
            "gh pr reopen 67176", "reopened #67176\n", "", "completed", cwd="/repo"
        )
        assert artifact.external_url == (
            "https://github.com/mistralai/dashboard/pull/67176"
        )

    @pytest.mark.asyncio
    async def test_reopen_without_checkout_is_skipped(
        self, service: SessionArtifactService
    ):
        artifact = await service.on_shell_effect(
            "gh pr reopen 67176",
            "✓ Reopened pull request mistralai/dashboard#67176\n",
            "",
            "completed",
            cwd=None,
        )
        assert artifact == []


class TestWireProjection:
    """The wire projection converts the storage checks model explicitly."""

    def test_to_public_artifact_with_checks(self):
        from vibe.app_server._session_artifacts import _to_public_checks
        from vibe.app_server.protocol import PublicChecksSummary
        from vibe.core.session.artifacts import ChecksSummary, CheckStatus

        checks = ChecksSummary(
            state="fail",
            checked_at="2026-10-21T15:11:56+00:00",
            checks=[
                CheckStatus(name="ci / test", status="fail", url="https://ci/1"),
                CheckStatus(name="lint", status="pass", url=""),
            ],
        )
        projected = _to_public_checks(checks)
        assert isinstance(projected, PublicChecksSummary)
        assert projected.state == "fail"
        assert projected.checked_at == "2026-10-21T15:11:56+00:00"
        assert projected.checks[0].name == "ci / test"

        # And the wire model accepts the projection (no model-class mismatch).
        details = PublicGitHubPullRequestDetails(
            repo_host="github.com",
            repo_owner="mistralai",
            repo_name="vibe",
            pull_request_number=123,
            state="open",
            source_branch="fix/login",
            target_branch="main",
            author="pierre",
            provider_created_at=None,
            provider_updated_at=None,
            provider_closed_at=None,
            provider_merged_at=None,
            merge_commit_sha=None,
            pr_status=None,
            checks=projected,
        )
        assert details.checks is not None
        assert details.checks.checks[1].status == "pass"

    def test_to_public_checks_none(self):
        from vibe.app_server._session_artifacts import _to_public_checks

        assert _to_public_checks(None) is None
