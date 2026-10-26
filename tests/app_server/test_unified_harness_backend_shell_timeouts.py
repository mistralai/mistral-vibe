"""Unified Harness adapter tests for shell timeouts: what Vibe's config sets is
what the Harness Core runs and states in each shell tool's schema.

Split from ``test_unified_harness_backend_adapter.py``, which keeps the adapter's
core behaviour.
"""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Literal

import pytest
import tomli_w

from vibe.app_server import _runtime as runtime_module
from vibe.app_server.protocol import SessionOptions
from vibe.core.tools.builtins.bash import BashToolConfig
from vibe.core.tools.builtins.experimental_bash import ExperimentalBashToolConfig
from vibe.core.tools.builtins.git_bash import GitBashToolConfig
from vibe.core.tools.builtins.windows_shell import WindowsShellToolConfig


@pytest.mark.parametrize(
    ("profile", "tool_settings", "expected"),
    [
        ("unix", None, (120, 300)),
        ("unix", {"permission": "ask"}, (120, 300)),
        (
            "powershell",
            {"default_timeout": 300, "max_timeout_seconds": 600},
            (300, 600),
        ),
        ("unix", {"default_timeout": 300, "max_timeout_seconds": 1_200}, (300, 1_200)),
        ("git_bash", {"default_timeout": 60, "max_timeout_seconds": 450.0}, (60, 450)),
        ("unix", {"default_timeout": 300, "max_timeout_seconds": 1_800}, (300, 1_800)),
        (
            "unix",
            {"default_timeout": "300", "max_timeout_seconds": "1800"},
            (300, 1_800),
        ),
        (
            "unix",
            {"default_timeout": 60, "max_timeout_seconds": 9_007_199_254_740_991},
            (60, 9_007_199_254_740_991),
        ),
        (
            "unix",
            {"default_timeout": 60, "max_timeout_seconds": "9007199254740991"},
            (60, 9_007_199_254_740_991),
        ),
        (
            "unix",
            {"default_timeout": 60, "max_timeout_seconds": "9007199254740991.0"},
            (60, 9_007_199_254_740_991),
        ),
    ],
)
@pytest.mark.asyncio
async def test_unified_runtime_states_and_passes_vibes_bash_timeouts(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: Literal["unix", "git_bash", "powershell"],
    tool_settings: dict[str, object] | None,
    expected: tuple[int, int],
) -> None:
    """*Prepare*: A command tool for one shell profile, with both timeouts set in
    `config.toml`, or neither; the longest maximum is the longest a session
    starts with, 2**53 - 1.
    *Do*: Derive the Core configuration used to start the session, and start a
    turn with it.
    *Assert*: Core always gets both timeouts, exactly: Vibe's 120 and 300
    seconds, or those set in `config.toml`; the command tool's schema states the
    same, and the configuration hashes as the local harness hashes it at
    session start.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness import HarnessSession
    from mistralai_vibe_local_harness.protocol import (
        RustBashTimeoutSettings,
        RustDeterminismContext,
        RustHarnessInput,
        RustTextContentBlock,
        RustUserMessageEvent,
        parse_apply_result,
    )
    from mistralai_vibe_local_harness.vibe import BASH_TIMEOUT
    from mistralai_vibe_local_harness.vibe._storage import canonical_json
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    tool_name = "bash" if profile == "unix" else profile
    if tool_settings is not None:
        config.setdefault("tools", {})[tool_name] = tool_settings
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(
        "vibe.app_server._runtime._command_environment_mode", lambda: profile
    )
    process = runtime_module.HarnessProcess()
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )

        # Do
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    # Assert
    assert (BASH_TIMEOUT.default_seconds, BASH_TIMEOUT.max_seconds) == (120, 300)
    assert derivation.core_config.settings.tools.bash_timeout == (
        RustBashTimeoutSettings(default_seconds=expected[0], max_seconds=expected[1])
    )
    canonical_json(
        derivation.core_config.model_dump(
            mode="json", by_alias=True, exclude_none=False
        )
    )
    session = HarnessSession.create(
        derivation.core_config.model_dump_json(exclude_none=True), "[]"
    )
    try:
        applied = session.apply(
            RustHarnessInput(
                input_id=1,
                determinism=RustDeterminismContext(
                    time_unix_ms=1_700_000_000_000, random_seed=1
                ),
                command=RustUserMessageEvent(
                    turn_id="turn-1",
                    mode="queue",
                    content=[RustTextContentBlock(text="hello")],
                ),
            ).model_dump_json(exclude_none=False)
        )
    finally:
        session.close()
    result = parse_apply_result(applied).model_dump(mode="json")
    action = result["transition"]["next"]["directives"][0]["action"]
    timeout = next(
        tool["parameters"]["properties"]["timeout_seconds"]
        for tool in action["model_input"]["tool_catalog"]["tools"]
        if "timeout_seconds" in tool["parameters"].get("properties", {})
    )
    assert (timeout["default"], timeout["maximum"]) == expected
    assert f"{expected[0]} by default, at most {expected[1]}" in timeout["description"]


@pytest.mark.parametrize(
    "config_class",
    [
        BashToolConfig,
        ExperimentalBashToolConfig,
        GitBashToolConfig,
        WindowsShellToolConfig,
    ],
)
def test_every_shell_tool_config_declares_both_timeouts_and_passes_them_to_core(
    config_class: type[BashToolConfig],
) -> None:
    """*Prepare*: A shell tool's settings with both timeouts set, as quoted text.
    *Do*: Read them as that tool's config, and work out Core's bash timeouts.
    *Assert*: The tool's config declares both settings and reads them as
    numbers, and Core gets both.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import RustBashTimeoutSettings

    settings = {"default_timeout": "300", "max_timeout_seconds": "1800"}

    # Do
    tool_config = config_class.model_validate(settings)
    bash_timeout = runtime_module._bash_timeout("bash", settings)

    # Assert
    assert {"default_timeout", "max_timeout_seconds"} <= set(config_class.model_fields)
    assert (tool_config.default_timeout, tool_config.max_timeout_seconds) == (
        300,
        1_800.0,
    )
    assert bash_timeout == RustBashTimeoutSettings(
        default_seconds=300, max_seconds=1_800
    )
