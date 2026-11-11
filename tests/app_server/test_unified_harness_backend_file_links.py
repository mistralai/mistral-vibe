from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.local_sandbox import RecordingSandbox
from vibe.app_server import _runtime as runtime_module
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.protocol import SessionOptions
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.utils import AgentEntrypoint


@pytest.mark.parametrize(
    "variant", ["cli", "cli_2026-07_v2", "cli_2026-08_v3", "cli_ml4_2026-10_v2"]
)
@pytest.mark.parametrize("base", [None, "Custom agent instructions."])
def test_local_desktop_file_guidance_preserves_the_selected_prompt(
    tmp_path: Path, variant: str, base: str | None
) -> None:
    from mistralai_vibe_local_harness.vibe import build_vibe_code_system_instructions
    from vibe.app_server._surface_instructions import with_surface_instructions

    config = build_test_vibe_config(
        system_prompt_id=variant, include_commit_signature=False
    )
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    extra = "Answer in French."
    instructions = runtime_module._build_unified_system_instructions(
        config,
        harness_files,
        cwd=tmp_path,
        surface="desktop",
        base=base,
        extra=extra,
        local_desktop=True,
    )
    expected_base = base or with_surface_instructions(
        build_vibe_code_system_instructions(variant=variant),
        variant=variant,
        surface="desktop",
    )

    assert instructions == "\n\n".join([
        expected_base,
        runtime_module._DESKTOP_FILE_LINK_INSTRUCTIONS.strip(),
        extra,
    ])
    assert "[src/main.ts:42](file:///absolute/worktree/src/main.ts)" in instructions
    assert "file:///C:/absolute/path" in instructions
    assert "Percent-encode spaces" in instructions
    assert "active worktree" in instructions
    assert "Keep line numbers in the link label" in instructions
    assert "files outside the current session workspace" in instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["desktop", "cli", "acp", "programmatic"])
@pytest.mark.parametrize("include_project_context", [False, True])
async def test_only_local_desktop_sessions_receive_file_link_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    entrypoint: AgentEntrypoint,
    include_project_context: bool,
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("VIBE_INCLUDE_PROJECT_CONTEXT", str(include_project_context))
    monkeypatch.setenv("VIBE_ENABLE_PR_MONITORING", "false")
    process = runtime_module.HarnessProcess(services=process_services)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path)), entrypoint=entrypoint
        )
        instructions = context.derive(
            UnifiedSessionSettings()
        ).core_config.system_instructions
    finally:
        await process.close()

    assert instructions.count("## Local Desktop file references") == (
        1 if entrypoint == "desktop" else 0
    )


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="the sandbox runs POSIX commands")
@pytest.mark.parametrize("include_project_context", [False, True])
async def test_sandboxed_desktop_sessions_do_not_receive_local_file_link_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    include_project_context: bool,
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("VIBE_INCLUDE_PROJECT_CONTEXT", str(include_project_context))
    monkeypatch.setenv("VIBE_ENABLE_PR_MONITORING", "false")
    process = runtime_module.HarnessProcess(
        sandbox=RecordingSandbox(tmp_path), services=process_services
    )
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path)), entrypoint="desktop"
        )
        instructions = context.derive(
            UnifiedSessionSettings()
        ).core_config.system_instructions
    finally:
        await process.close()

    assert "## Local Desktop file references" not in instructions
