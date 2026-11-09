"""Unified Harness adapter tests for the shell guidance in system instructions:
the process tools' guidance only with background processes, the foreground
guidance otherwise.

Split from ``test_unified_harness_backend_adapter.py``, which keeps the adapter's
core behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server import _runtime as runtime_module


@pytest.mark.parametrize(
    ("variant", "prompt_file"),
    [
        ("cli", None),
        ("cli_2026-07_v2", None),
        ("cli", "cli.md"),
        ("cli", "cli_2026-07_v2.md"),
    ],
)
def test_unified_system_instructions_advise_backgrounding_only_with_background_processes(
    tmp_path: Path, variant: str, prompt_file: str | None
) -> None:
    """*Prepare*: A built-in prompt variant or a prompt file that carries the
    shell guidance.
    *Do*: Resolve Unified system instructions with background processes, then
    without.
    *Assert*: Only with them does the shell guidance say to start long work with
    the process tools; without, it is the neutral guidance, once.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness import process_tools_shell_guidance
    from mistralai_vibe_local_harness.vibe._system_instructions import (
        FOREGROUND_SHELL_GUIDANCE,
    )
    from vibe.core.config.harness_files import HarnessFilesManager

    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    config = build_test_vibe_config(system_prompt_id=variant)
    base = (
        None
        if prompt_file is None
        else (
            Path(runtime_module.__file__).parents[1] / "core" / "prompts" / prompt_file
        ).read_text(encoding="utf-8")
    )

    background, foreground = (
        runtime_module._build_unified_system_instructions(
            config, harness_files, cwd=tmp_path, base=base, background_processes=enabled
        )
        for enabled in (True, False)
    )

    guidance = process_tools_shell_guidance(
        background_processes=True, command_environment="unix"
    )
    assert guidance is not None
    assert background.count(guidance) == 1
    assert FOREGROUND_SHELL_GUIDANCE not in background
    assert foreground.count(FOREGROUND_SHELL_GUIDANCE) == 1
    assert "tools.process" not in foreground


def test_every_bundled_prompt_with_shell_guidance_opens_it_with_the_foreground_guidance() -> (
    None
):
    """*Prepare*: The prompt files Vibe bundles.
    *Do*: Pick the ones with a **Shell** section.
    *Assert*: They are the files the test above fits, and each carries the
    foreground guidance the Harness Core's process-tools guidance replaces, once.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._system_instructions import (
        FOREGROUND_SHELL_GUIDANCE,
        SHELL_GUIDANCE_HEADING,
    )

    prompts = Path(runtime_module.__file__).parents[1] / "core" / "prompts"

    with_shell_section = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(prompts.glob("*.md"))
        if SHELL_GUIDANCE_HEADING in path.read_text(encoding="utf-8")
    }

    assert set(with_shell_section) == {"cli.md", "cli_2026-07_v2.md"}
    for text in with_shell_section.values():
        assert text.count(FOREGROUND_SHELL_GUIDANCE) == 1
