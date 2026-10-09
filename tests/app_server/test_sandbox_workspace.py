"""A sandboxed session reads its workspace context through its adapter.

The adapter runs every command in a real subprocess and reads files from disk,
so a sandboxed session and a host session over the same directory must see the
same git metadata and AGENTS.md docs.
"""

from __future__ import annotations

from pathlib import Path
import shlex
import sys

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from tests.stubs.local_sandbox import (
    ROOT_DOC,
    SUB_DOC,
    RecordingSandbox,
    build_trusted_project,
)
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
from vibe.app_server.protocol import SessionOptions
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.trusted_folders import trusted_folders_manager

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the sandbox runs POSIX command lines"
)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build_trusted_project(tmp_path / "project", monkeypatch)


@pytest.mark.asyncio
async def test_a_sandboxed_session_reads_the_same_workspace_context_through_its_adapter(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A trusted host session and a sandboxed session over the same
    repository, the sandboxed one configured to offer background processes.
    *Do*: Derive both sessions' Core and Runtime configuration.
    *Assert*: The system instructions match, the sandboxed one came through the
    adapter, its background processes run in the sandbox, and no host
    environment travels to its commands.
    """
    # Prepare
    sandbox = RecordingSandbox(project)
    host_process = HarnessProcess(
        HarnessFilesManager(sources=("user", "project")), experimental_harness=True
    )
    monkeypatch.setenv("VIBE_ENABLE_BACKGROUND_PROCESSES", "true")
    sandbox_process = HarnessProcess(experimental_harness=True, sandbox=sandbox)

    # Do
    host = (
        await host_process.build_unified_session_context(
            SessionOptions(cwd=str(project), trust_workspace=True)
        )
    ).derive(UnifiedSessionSettings())
    sandboxed_context = await sandbox_process.build_unified_session_context(
        SessionOptions()
    )
    sandboxed = sandboxed_context.derive(UnifiedSessionSettings())

    # Assert
    instructions = sandboxed.core_config.system_instructions
    assert instructions == host.core_config.system_instructions
    assert ROOT_DOC in instructions
    assert "Add the notes" in instructions
    assert "main" in instructions
    assert str(project / "AGENTS.md") in sandbox.reads
    assert any(
        shlex.split(command)[:4]
        == ["git", "-c", "core.fsmonitor=", "--no-optional-locks"]
        for command in sandbox.commands
    )
    assert sandboxed.adapter_config.sandbox is sandbox
    assert sandboxed.adapter_config.workspace.cwd == project
    assert sandboxed.adapter_config.process_authority == "sandbox"
    assert sandboxed.adapter_config.env == {}
    assert host.adapter_config.env
    tools = sandboxed.core_config.settings.tools
    assert tools.background_processes.mode == "enabled"
    assert sandboxed_context.config_orchestrator.config.enable_background_processes
    assert host.core_config.settings.tools.background_processes.mode == "enabled"
    assert tools.command_environment.mode == "unix"
    await host_process.close()
    await sandbox_process.close()


@pytest.mark.asyncio
async def test_a_sandboxed_session_in_a_subdirectory_reads_docs_up_to_its_workspace(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A host session started in a subdirectory of a trusted repository,
    and a sandboxed one started in the same subdirectory of its workspace.
    *Do*: Derive both sessions' system instructions.
    *Assert*: They match, and both hold the subdirectory's AGENTS.md and the one
    above it at the repository root, read through the adapter.
    """
    # Prepare
    monkeypatch.setattr(trusted_folders_manager, "find_trust_root", lambda _: project)
    sandbox = RecordingSandbox(project)
    host_process = HarnessProcess(
        HarnessFilesManager(sources=("user", "project")), experimental_harness=True
    )
    sandbox_process = HarnessProcess(experimental_harness=True, sandbox=sandbox)
    subdirectory = str(project / "sub")

    # Do
    host = (
        await host_process.build_unified_session_context(
            SessionOptions(cwd=subdirectory, trust_workspace=True)
        )
    ).derive(UnifiedSessionSettings())
    sandboxed = (
        await sandbox_process.build_unified_session_context(
            SessionOptions(cwd=subdirectory)
        )
    ).derive(UnifiedSessionSettings())

    # Assert
    instructions = sandboxed.core_config.system_instructions
    assert instructions == host.core_config.system_instructions
    assert ROOT_DOC in instructions
    assert SUB_DOC in instructions
    assert str(project / "AGENTS.md") in sandbox.reads
    await host_process.close()
    await sandbox_process.close()


@pytest.mark.asyncio
async def test_a_sandboxed_session_without_git_says_so_like_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A sandbox workspace that is not a git repository.
    *Do*: Derive the session's system instructions.
    *Assert*: The project context reports no repository instead of failing.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    process = HarnessProcess(
        experimental_harness=True, sandbox=RecordingSandbox(tmp_path)
    )

    # Do
    derived = (await process.build_unified_session_context(SessionOptions())).derive(
        UnifiedSessionSettings()
    )

    # Assert
    assert "Not a git repository or git not available" in (
        derived.core_config.system_instructions
    )
    await process.close()
