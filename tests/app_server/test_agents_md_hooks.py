from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.protocol import (
    RustAlwaysHookSelector as Always,
    RustHarnessHookBinding,
    RustHookToolCall,
    RustPostToolCallHookInput,
    RustProtocolError,
    RustRuntimeBuiltinToolCall,
    RustTextContentBlock,
    RustToolFailureResult,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe import (
    CompiledHooks,
    HookContext,
    LocalRuntimeAdapterConfig,
)

from vibe.app_server._agents_md_hooks import (
    AGENTS_MD_HOOK_BINDING_ID,
    agents_md_hook,
    merge_agents_md_hook,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.utils import VIBE_WARNING_TAG


@pytest.fixture()
def _trusted_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
    monkeypatch.setattr(
        trusted_folders_manager, "find_trust_root", lambda path: path.resolve()
    )
    return tmp_path


def _context(tmp_path: Path, session_id: str = "session-1") -> HookContext:
    # Production adapter configs always list the cwd among workspace_roots
    # (it is the file sandbox), so tests default to that shape: trust-gating
    # regressions surface instead of hiding behind the empty default.
    return HookContext(
        config=LocalRuntimeAdapterConfig(cwd=tmp_path, workspace_roots=(tmp_path,)),
        messages=(),
        session_id=session_id,
    )


def _read_call(path: str) -> RustHookToolCall:
    return RustHookToolCall(
        action_id="action-1",
        call_id="call-1",
        call=RustRuntimeBuiltinToolCall(
            name="file_system.read_file", arguments={"path": path}
        ),
    )


def _hook_input(
    call: RustHookToolCall, result: RustToolSuccessResult | RustToolFailureResult
) -> RustPostToolCallHookInput:
    return RustPostToolCallHookInput(tool_call=call, tool_result=result)


def _success() -> RustToolSuccessResult:
    return RustToolSuccessResult(content=[RustTextContentBlock(text="hello")])


@pytest.mark.asyncio
async def test_read_appends_undiscovered_agents_md(
    tmp_path: Path, _trusted_project: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("# Sub instructions", encoding="utf-8")
    (sub / "file.py").write_text("hello", encoding="utf-8")

    handler = agents_md_hook(
        HarnessFilesManager(sources=("user", "project"), cwd=tmp_path)
    )
    result = await handler(
        _hook_input(_read_call("sub/file.py"), _success()), _context(tmp_path)
    )

    appended = result.output.tool_result.content[-1]
    assert isinstance(appended, RustTextContentBlock)
    assert appended.text.startswith(f"<{VIBE_WARNING_TAG}>")
    assert "Contents of" in appended.text
    assert "sub/AGENTS.md" in appended.text
    assert "# Sub instructions" in appended.text
    # The read's own content is preserved ahead of the injected section.
    original = result.output.tool_result.content[0]
    assert isinstance(original, RustTextContentBlock)
    assert original.text == "hello"


@pytest.mark.asyncio
async def test_injection_deduplicates_per_session(
    tmp_path: Path, _trusted_project: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("# Sub", encoding="utf-8")
    (sub / "a.py").write_text("a", encoding="utf-8")
    (sub / "b.py").write_text("b", encoding="utf-8")

    handler = agents_md_hook(
        HarnessFilesManager(sources=("user", "project"), cwd=tmp_path)
    )
    first = await handler(
        _hook_input(_read_call("sub/a.py"), _success()), _context(tmp_path)
    )
    second = await handler(
        _hook_input(_read_call("sub/b.py"), _success()), _context(tmp_path)
    )
    assert len(first.output.tool_result.content) == 2
    # Same session: the directory's doc is already injected, so the result
    # passes through untouched.
    assert len(second.output.tool_result.content) == 1

    # A different session (or subagent) starts with a fresh dedup set.
    third = await handler(
        _hook_input(_read_call("sub/b.py"), _success()),
        _context(tmp_path, session_id="session-2"),
    )
    assert len(third.output.tool_result.content) == 2


@pytest.mark.asyncio
async def test_non_read_calls_and_failures_pass_through(
    tmp_path: Path, _trusted_project: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("# Sub", encoding="utf-8")
    (sub / "file.py").write_text("hello", encoding="utf-8")

    handler = agents_md_hook(
        HarnessFilesManager(sources=("user", "project"), cwd=tmp_path)
    )

    write_call = RustHookToolCall(
        action_id="action-1",
        call_id="call-1",
        call=RustRuntimeBuiltinToolCall(
            name="file_system.write_file",
            arguments={"path": "sub/file.py", "content": "x"},
        ),
    )
    write_result = await handler(
        _hook_input(write_call, _success()), _context(tmp_path)
    )
    assert len(write_result.output.tool_result.content) == 1

    failure = RustToolFailureResult(
        content=[], error=RustProtocolError(code="io", message="boom", retryable=False)
    )
    failed_read = await handler(
        _hook_input(_read_call("sub/file.py"), failure), _context(tmp_path)
    )
    assert failed_read.output.tool_result is failure


@pytest.mark.asyncio
async def test_untrusted_cwd_does_not_inject_despite_production_roots(
    tmp_path: Path, _trusted_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the trust gate: production adapter configs list
    the cwd among workspace_roots, but an untrusted cwd must still inject
    nothing — the handler drops the cwd from the roots it passes to
    ``for_session`` so the trust gate stays in charge of it.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("# Outside", encoding="utf-8")
    (outside / "file.py").write_text("hello", encoding="utf-8")

    # Untrusted project layer: the cwd contributes no project root.
    monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)
    handler = agents_md_hook(
        HarnessFilesManager(sources=("user", "project"), cwd=tmp_path)
    )
    result = await handler(
        _hook_input(_read_call("outside/file.py"), _success()), _context(tmp_path)
    )
    assert len(result.output.tool_result.content) == 1


def test_merge_binds_the_hook_without_touching_handlers() -> None:
    merged = merge_agents_md_hook(CompiledHooks())
    assert [binding.id for binding in merged.bindings] == [AGENTS_MD_HOOK_BINDING_ID]
    assert merged.bindings[0].point == "post_tool_call"
    # The handler stays Host-global: a session's handler map is the foreign
    # channel, which surfaces public run notices a builtin must not emit.
    assert not merged.handlers.post_tool_call


def test_merge_takes_an_order_unique_against_foreign_bindings() -> None:
    # Foreign hooks are numbered 0..n-1 by enumerate and the Core rejects a
    # duplicate order across all bindings, so the builtin must slot after the
    # last one — a constant order would break every session with user hooks.
    foreign = RustHarnessHookBinding(
        id="user:guard", point="pre_tool_call", order=0, selector=Always()
    )
    merged = merge_agents_md_hook(
        CompiledHooks(bindings=(foreign, foreign.model_copy(update={"order": 3})))
    )
    builtin = merged.bindings[-1]
    assert builtin.id == AGENTS_MD_HOOK_BINDING_ID
    assert builtin.order == 4
    assert [binding.order for binding in merged.bindings] == [0, 3, 4]
