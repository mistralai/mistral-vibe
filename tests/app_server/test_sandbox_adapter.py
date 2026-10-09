"""Sessions whose workspace lives behind a Sandbox Adapter.

The adapter here runs every command in a real subprocess and reads files from
disk, so a sandboxed session and a host session over the same directory must
see the same workspace. What differs is the path the data takes, which the
adapter records.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.protocol import (
    RustHookToolCall,
    RustPostToolCallHookInput,
    RustPostToolCallHookResult,
    RustRuntimeBuiltinToolCall,
    RustRuntimeBuiltinToolName,
    RustTextContentBlock,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe import HookContext, LocalRuntimeAdapterConfig
from tests.stubs.local_sandbox import (
    EXECUTE_COMPLETION,
    SANDBOX_ONLY_VARIABLE,
    SUB_DOC,
    RecordingSandbox,
    Script,
    ScriptedModel,
    build_trusted_project,
)
from vibe.app_server._agents_md_hooks import agents_md_hook
from vibe.app_server._runtime import HarnessProcess
from vibe.app_server.local import (
    ClientDescriptor,
    LocalHarnessHost,
    LocalHarnessOptions,
)
from vibe.app_server.protocol import ClientCapabilities, ClientInfo, SessionOptions
from vibe.app_server.run_export import HeadlessUsageError, RunLimits, RunOutcome
from vibe.cli.headless_run import StopRequests
from vibe.cli.programmatic import run_headless
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config.harness_files import HarnessFilesManager

pytestmark = [
    # Whole sessions with subprocess tools: slower than a unit test under load.
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="the sandbox runs POSIX command lines"
    ),
]

_PARENT_TASK = "PARENT_TASK"
_CHILD_TASK = "CHILD_TASK"

_PARENT_PROGRAM = f"""
async function main() {{
  let processes;
  try {{
    processes = tools.process && tools.process.start
      ? await tools.process.start({{command: "sleep 1"}})
      : "process tools off";
  }} catch (error) {{
    processes = "failed: " + error.message;
  }}
  await tools.subagent.spawn({{agentName: "reader", message: {json.dumps(_CHILD_TASK)}}});
  const child = await tools.subagent.wait({{agentName: "reader", timeoutMs: 30000}});
  return {{processes: JSON.stringify(processes), child}};
}}
"""

_CHILD_PROGRAM = f"""
async function main() {{
  const notes = await tools.file_system.read_file({{path: "sub/notes.txt"}});
  const mark = await tools.file_system.bash({{command: "echo ${SANDBOX_ONLY_VARIABLE}"}});
  return {{notes, mark}};
}}
"""

# Each task's tool calls, in order, before its final answer. The parent reads
# with a direct call, whose result the AGENTS.md hook extends.
_SCRIPTS: dict[str, Script] = {
    _PARENT_TASK: [
        ("read_file", {"path": "sub/notes.txt"}),
        ("run_typescript", {"code": _PARENT_PROGRAM}),
    ],
    _CHILD_TASK: [("run_typescript", {"code": _CHILD_PROGRAM})],
}


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build_trusted_project(tmp_path / "project", monkeypatch)


def test_a_sandboxed_session_never_loads_project_files(tmp_path: Path) -> None:
    """*Prepare*: Project config, skills and hooks would come from the host's disk.
    *Do*: Build a sandboxed harness process that asks for them, and one on the legacy harness.
    *Assert*: Both are refused.
    """
    sandbox = RecordingSandbox(tmp_path)

    with pytest.raises(HeadlessUsageError, match="cannot load project files"):
        HarnessProcess(
            HarnessFilesManager(sources=("user", "project")),
            experimental_harness=True,
            sandbox=sandbox,
        )
    with pytest.raises(HeadlessUsageError, match="requires the Unified Harness"):
        HarnessProcess(legacy_harness=True, sandbox=sandbox)


@pytest.mark.asyncio
async def test_the_agents_md_hook_reads_subdirectory_docs_through_the_adapter(
    project: Path,
) -> None:
    """*Prepare*: The AGENTS.md hook on a host session and on a sandboxed one.
    *Do*: Read a file in a subdirectory twice in each session.
    *Assert*: Both append the same doc once, and the sandboxed one read it through the adapter.
    """
    # Prepare
    sandbox = RecordingSandbox(project)
    hook = agents_md_hook(HarnessFilesManager(sources=("user", "project")))
    host = LocalRuntimeAdapterConfig.at(cwd=project, roots=(project,))
    sandboxed = LocalRuntimeAdapterConfig.at(
        cwd=project, roots=(project,), sandbox=sandbox
    )

    # Do
    results = []
    for session_id, config in (("host", host), ("sandbox", sandboxed)):
        context = HookContext(config=config, messages=(), session_id=session_id)
        for _ in range(2):
            result = await hook(_read_input("sub/notes.txt"), context)
            results.append([
                block.text
                for block in result.output.tool_result.content
                if isinstance(block, RustTextContentBlock)
            ])

    # Assert
    host_first, host_second, sandbox_first, sandbox_second = results
    assert sandbox_first == host_first
    assert SUB_DOC in sandbox_first[-1]
    assert host_second == sandbox_second == ["first note"]
    assert sandbox.reads == [str(project / "sub" / "AGENTS.md")]


@pytest.mark.parametrize(
    ("write_tool", "write_arguments"),
    [
        (
            "file_system.write_file",
            {"path": "pkg/mod/AGENTS.md", "content": "# Module rules"},
        ),
        (
            "file_system.search_replace",
            {
                "file_path": "pkg/mod/AGENTS.md",
                "content": [{"old_str": "", "new_str": "# Module rules"}],
            },
        ),
    ],
    ids=["write_file", "search_replace"],
)
@pytest.mark.asyncio
async def test_the_agents_md_hook_looks_in_each_sandbox_directory_once(
    project: Path,
    write_tool: RustRuntimeBuiltinToolName,
    write_arguments: dict[str, Any],
) -> None:
    """*Prepare*: A sandboxed session over a tree with no AGENTS.md below its root.
    *Do*: Read files in it repeatedly, between Vibe's own writes of AGENTS.md
    files and bash commands, one naming AGENTS.md.
    *Assert*: The sandbox is asked for each directory's doc once, and again
    only after a write of that doc or a command naming one; the docs written
    are appended.
    """
    # Prepare
    deep = project / "pkg" / "mod"
    deep.mkdir(parents=True)
    for name in ("a.py", "b.py"):
        (deep / name).write_text("x\n", encoding="utf-8")
    sandbox = RecordingSandbox(project)
    hook = agents_md_hook(HarnessFilesManager(sources=("user",)))
    config = LocalRuntimeAdapterConfig.at(
        cwd=project, roots=(project,), sandbox=sandbox
    )
    context = HookContext(config=config, messages=(), session_id="sandbox")
    pkg_doc, mod_doc = str(project / "pkg" / "AGENTS.md"), str(deep / "AGENTS.md")

    # Do
    for read in ("pkg/mod/a.py", "pkg/mod/b.py", "pkg/mod/a.py", "pkg/x.py"):
        assert _appended(await hook(_read_input(read), context)) == []
    first_lookups = list(sandbox.reads)
    (deep / "AGENTS.md").write_text("# Module rules", encoding="utf-8")
    await hook(_call_input(write_tool, write_arguments), context)
    after_write = _appended(await hook(_read_input("pkg/mod/a.py"), context))
    lookups_after_write = sandbox.reads[len(first_lookups) :]
    await hook(_call_input("file_system.bash", {"command": "ls pkg"}), context)
    await hook(_read_input("pkg/mod/b.py"), context)
    lookups_after_other_command = sandbox.reads[
        len(first_lookups) + len(lookups_after_write) :
    ]
    (project / "pkg" / "AGENTS.md").write_text("# Package rules", encoding="utf-8")
    await hook(
        _call_input(
            "file_system.bash", {"command": "echo '# Package rules' > pkg/AGENTS.md"}
        ),
        context,
    )
    seen = len(sandbox.reads)
    after_command = _appended(await hook(_read_input("pkg/mod/b.py"), context))
    lookups_after_command = sandbox.reads[seen:]

    # Assert
    assert sorted(first_lookups) == [pkg_doc, mod_doc]
    assert lookups_after_write == [mod_doc]
    assert len(after_write) == 1 and "# Module rules" in after_write[0]
    assert lookups_after_other_command == []
    assert sorted(lookups_after_command) == [pkg_doc, mod_doc]
    assert len(after_command) == 1 and "# Package rules" in after_command[0]
    assert "# Module rules" not in after_command[0]


class _HeldReadsSandbox(RecordingSandbox):
    """A Recording Sandbox whose file reads answer only once ``release`` is
    set, with the file as it was when they were asked.
    """

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.release = asyncio.Event()
        self.waiting = asyncio.Event()

    async def read_file(self, path: str, max_bytes: int) -> bytes | None:
        content = await super().read_file(path, max_bytes)
        self.waiting.set()
        await self.release.wait()
        return content


class _FailingReadsSandbox(RecordingSandbox):
    """A Recording Sandbox whose first file read fails."""

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.failed = False

    async def read_file(self, path: str, max_bytes: int) -> bytes | None:
        if not self.failed:
            self.failed = True
            raise ConnectionError("sandbox is gone")
        return await super().read_file(path, max_bytes)


@pytest.mark.asyncio
async def test_a_doc_written_while_its_directory_is_read_is_looked_up_again(
    project: Path,
) -> None:
    """*Prepare*: A sandboxed session over a tree with no AGENTS.md below its
    root, whose sandbox holds file reads until released.
    *Do*: Read a file; while the hook waits for the directory's doc, write
    that doc with Vibe's own tool; then release the read and read again.
    *Assert*: The first read, which found no doc, appends none; the second
    looks the directory up again and appends it: the first lookup, back after
    the write, did not mark the directory as checked.
    """
    # Prepare
    (project / "pkg").mkdir()
    (project / "pkg" / "a.py").write_text("x\n", encoding="utf-8")
    sandbox = _HeldReadsSandbox(project)
    hook = agents_md_hook(HarnessFilesManager(sources=("user",)))
    config = LocalRuntimeAdapterConfig.at(
        cwd=project, roots=(project,), sandbox=sandbox
    )
    context = HookContext(config=config, messages=(), session_id="sandbox")
    doc = project / "pkg" / "AGENTS.md"

    # Do
    first = asyncio.ensure_future(hook(_read_input("pkg/a.py"), context))
    await sandbox.waiting.wait()
    doc.write_text("# Package rules", encoding="utf-8")
    await hook(
        _call_input(
            "file_system.write_file",
            {"path": "pkg/AGENTS.md", "content": "# Package rules"},
        ),
        context,
    )
    sandbox.release.set()
    first_appended = _appended(await first)
    second_appended = _appended(await hook(_read_input("pkg/a.py"), context))

    # Assert
    assert sandbox.reads == [str(doc), str(doc)]
    assert first_appended == []
    assert len(second_appended) == 1 and "# Package rules" in second_appended[0]


@pytest.mark.asyncio
async def test_a_directory_whose_doc_read_failed_is_looked_up_again(
    project: Path,
) -> None:
    """*Prepare*: A sandboxed session over a tree with an AGENTS.md below its
    root, whose sandbox fails the first file read.
    *Do*: Read a file in that directory twice.
    *Assert*: The second read looks the directory up again and appends its
    doc: the failed lookup did not mark it as checked.
    """
    # Prepare
    (project / "pkg").mkdir()
    (project / "pkg" / "a.py").write_text("x\n", encoding="utf-8")
    (project / "pkg" / "AGENTS.md").write_text("# Package rules", encoding="utf-8")
    sandbox = _FailingReadsSandbox(project)
    hook = agents_md_hook(HarnessFilesManager(sources=("user",)))
    config = LocalRuntimeAdapterConfig.at(
        cwd=project, roots=(project,), sandbox=sandbox
    )
    context = HookContext(config=config, messages=(), session_id="sandbox")

    # Do
    first = _appended(await hook(_read_input("pkg/a.py"), context))
    second = _appended(await hook(_read_input("pkg/a.py"), context))

    # Assert
    assert first == []
    assert len(second) == 1 and "# Package rules" in second[0]


@pytest.mark.parametrize("sandboxed", [False, True], ids=["host", "sandbox"])
@pytest.mark.asyncio
async def test_a_subagent_works_in_its_parents_workspace(
    project: Path, monkeypatch: pytest.MonkeyPatch, sandboxed: bool
) -> None:
    """*Prepare*: A scripted parent that reads a file, tries a process tool and
    spawns a subagent that reads a file and runs a command.
    *Do*: Run it headless on the host and against a Sandbox Adapter.
    *Assert*: Both see the same workspace and docs and start the process, the
    sandboxed ones through the adapter.
    """
    # Prepare
    scripted = ScriptedModel(_SCRIPTS)
    monkeypatch.setattr(EXECUTE_COMPLETION, scripted)
    sandbox = RecordingSandbox(project) if sandboxed else None
    monkeypatch.setenv("XDG_STATE_HOME", str(project.parent / "state"))
    host = LocalHarnessHost()

    # Do
    report = await run_headless(
        harness_options=_options(project, sandbox),
        prompt=_PARENT_TASK,
        stop=StopRequests(RunLimits()),
        harness_host=host,
    )
    await host.close()

    # Assert
    assert report.result.outcome is RunOutcome.FINISHED, report.result.error
    parent = scripted.final_input(_PARENT_TASK)
    child = scripted.final_input(_CHILD_TASK)
    assert SUB_DOC in parent
    assert "first note" in child
    assert "processId" in parent
    if sandbox is None:
        return
    operations = sandbox.helper_operations
    assert str(project / "sub" / "AGENTS.md") in sandbox.reads
    tool_operations = [
        operation for operation in operations if not operation.startswith("skills-")
    ]
    assert "process" in tool_operations
    assert sorted(op for op in tool_operations if op != "process") == [
        "bash",
        "file",
        "file",
    ]
    assert "inside" in child


def _options(project: Path, sandbox: RecordingSandbox | None) -> LocalHarnessOptions:
    return LocalHarnessOptions(
        client=ClientDescriptor(
            info=ClientInfo(name="vibe_test", title="Vibe test", version="0"),
            capabilities=ClientCapabilities(callback_kinds=["approval", "user_input"]),
        ),
        session_options=SessionOptions(
            cwd=None if sandbox is not None else str(project),
            agent=BuiltinAgentName.AUTO_APPROVE,
            trust_workspace=sandbox is None,
            headless=True,
        ),
        experimental_harness=True,
        sandbox=sandbox,
    )


def _read_input(path: str) -> RustPostToolCallHookInput:
    return _call_input("file_system.read_file", {"path": path})


def _call_input(
    name: RustRuntimeBuiltinToolName, arguments: dict[str, Any]
) -> RustPostToolCallHookInput:
    return RustPostToolCallHookInput(
        tool_call=RustHookToolCall(
            action_id="action-1",
            call_id="call-1",
            call=RustRuntimeBuiltinToolCall(name=name, arguments=arguments),
        ),
        tool_result=RustToolSuccessResult(
            content=[RustTextContentBlock(text="first note")]
        ),
    )


def _appended(result: RustPostToolCallHookResult) -> list[str]:
    return [
        block.text
        for block in result.output.tool_result.content[1:]
        if isinstance(block, RustTextContentBlock)
    ]
