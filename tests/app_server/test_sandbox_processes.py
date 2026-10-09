"""Background processes of a session whose workspace lives behind a Sandbox
Adapter, compared with the same session on the host.

The adapter runs every command in a real subprocess, so the sandboxed
processes run under the sandbox's process server on this machine, and what the
model is told must match what a host session tells it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from tests.stubs.local_sandbox import (
    EXECUTE_COMPLETION,
    RecordingSandbox,
    ScriptedModel,
    build_trusted_project,
)
from vibe.app_server.local import (
    ClientDescriptor,
    LocalHarnessHost,
    LocalHarnessOptions,
)
from vibe.app_server.protocol import ClientCapabilities, ClientInfo, SessionOptions
from vibe.app_server.run_export import RunLimits, RunOutcome
from vibe.cli.headless_run import StopRequests
from vibe.cli.programmatic import run_headless
from vibe.core.agents.models import BuiltinAgentName

pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32", reason="the sandbox runs POSIX command lines"
    )
]

_PROCESS_TASK = "PROCESS_TASK"
# A grandchild that starts its own session, so it leaves the process group,
# ignores the signals a stop sends first, and records its pid. Its parent
# waits for it, so it stays a descendant.
_DAEMON = (
    "import os, signal, time; pid = os.fork(); pid or (os.setsid(),"
    " signal.signal(signal.SIGTERM, signal.SIG_IGN),"
    " signal.signal(signal.SIGHUP, signal.SIG_IGN),"
    " open('daemon.pid', 'w').write(str(os.getpid())),"
    " time.sleep(300), os._exit(0)); os.waitpid(pid, 0)"
)
_PROCESS_COMMANDS = {
    "short": "printf 'one\\ntwo\\n'; exit 3",
    "chat": (
        'stty -echo; printf \'ready in %s with %s\\n\' "$(pwd)" "$GREETING";'
        " IFS= read -r value; printf 'got:%s\\n' \"$value\"; sleep 30"
    ),
    # Leaves behind a process that ignores the signal a stop sends first, and
    # a daemon, and records their pids where the test can find them.
    "tree": (
        'sh -c \'trap "" TERM HUP; echo $$ > descendant.pid;'
        " while :; do sleep 1; done' &"
        f" {shlex.quote(sys.executable)} -c {shlex.quote(_DAEMON)} &"
        " while [ ! -s descendant.pid ] || [ ! -s daemon.pid ]; do sleep 0.1; done;"
        " printf 'descendant:started\\n'; wait"
    ),
    "check": (
        "if kill -0 $(cat descendant.pid) 2>/dev/null"
        " || kill -0 $(cat daemon.pid) 2>/dev/null;"
        " then echo alive; else echo gone; fi"
    ),
}
_PROCESS_PROGRAM = (
    f"const COMMANDS = {json.dumps(_PROCESS_COMMANDS)};\n"
    + """
async function main() {
  async function readUntil(processId, text, cursor) {
    let seen = "";
    let next = cursor;
    for (let attempt = 0; attempt < 50; attempt++) {
      const page = await tools.process.output({processId, cursor: next, waitMs: 2000});
      seen += page.output;
      next = page.nextCursor;
      if (seen.includes(text) || page.status !== "running") {
        return {seen, next, status: page.status, exitCode: page.exitCode};
      }
    }
    return {seen, next, status: "still waiting"};
  }
  async function failure(call) {
    try {
      return {succeeded: await call()};
    } catch (error) {
      return {failed: String(error)};
    }
  }
  const short = await tools.process.start({command: COMMANDS.short});
  const shortDone = await readUntil(short.processId, "never printed", 0);
  const shortWhole = await tools.process.output({processId: short.processId});
  const shortPage = await tools.process.output(
    {processId: short.processId, cursor: 4, maxBytes: 3}
  );
  const chat = await tools.process.start(
    {command: COMMANDS.chat, cwd: "sub", env: {GREETING: "hi"}}
  );
  const ready = await readUntil(chat.processId, "ready", 0);
  const written = await tools.process.write({processId: chat.processId, text: "hello\\n"});
  const got = await readUntil(chat.processId, "got:hello", ready.next);
  const tree = await tools.process.start({command: COMMANDS.tree});
  const treeReady = await readUntil(tree.processId, "descendant:started", 0);
  const treeStopped = await tools.process.stop({processId: tree.processId});
  const check = await tools.process.start({command: COMMANDS.check});
  const checked = await readUntil(check.processId, "never printed", 0);
  const chatStopped = await tools.process.stop({processId: chat.processId});
  const chatAfter = await tools.process.output({processId: chat.processId});
  const missingCwd = await failure(
    () => tools.process.start({command: "true", cwd: "missing-dir"})
  );
  const listed = await tools.process.list({});
  return {
    short, shortDone, shortWhole, shortPage, chat, ready, written, got, tree,
    treeReady, treeStopped, checked, chatStopped, chatAfter, missingCwd, listed,
  };
}
"""
)
_PROCESS_ID = re.compile(r"process-[0-9a-f]{12}-[0-9a-f]{24}")


def _options(workspace: Path, sandbox: RecordingSandbox | None) -> LocalHarnessOptions:
    return LocalHarnessOptions(
        client=ClientDescriptor(
            info=ClientInfo(name="vibe_test", title="Vibe test", version="0"),
            capabilities=ClientCapabilities(callback_kinds=["approval", "user_input"]),
        ),
        session_options=SessionOptions(
            cwd=None if sandbox is not None else str(workspace),
            agent=BuiltinAgentName.AUTO_APPROVE,
            trust_workspace=sandbox is None,
            headless=True,
        ),
        experimental_harness=True,
        sandbox=sandbox,
    )


async def _run(workspace: Path, sandbox: RecordingSandbox | None) -> RunOutcome:
    host = LocalHarnessHost()
    try:
        report = await run_headless(
            harness_options=_options(workspace, sandbox),
            prompt=_PROCESS_TASK,
            stop=StopRequests(RunLimits()),
            harness_host=host,
        )
    finally:
        await host.close()
    return report.result.outcome


def _tool_messages(model: ScriptedModel) -> list[dict[str, Any]]:
    return [
        message
        for message in model.final_requests[_PROCESS_TASK]
        if message["role"] == "tool"
    ]


def _comparable(workspace: Path, messages: list[dict[str, Any]]) -> str:
    text = json.dumps(messages)
    for form in {str(workspace.resolve()), str(workspace)}:
        text = text.replace(form, "<workspace>")
    return _PROCESS_ID.sub("<process>", text)


def _descendants_are_gone(workspace: Path) -> bool:
    for name in ("descendant.pid", "daemon.pid"):
        pid = int((workspace / name).read_text().strip())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        return False
    return True


@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_background_processes_answer_the_model_alike_on_the_host_and_in_a_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Two copies of a project, and a model whose program starts
    processes, reads their output with waits and cursors, writes to one, stops
    one that left a stubborn process and a daemonised grandchild behind,
    checks nothing of it survived, and lists them.
    *Do*: Run it headless on the host over one copy, and against a Sandbox
    Adapter over the other, as `vibe -p --agent-socket` does.
    *Assert*: The tool results the model was last sent match but for the
    workspace path and process ids, the stop left nothing behind on either
    side, and the sandboxed processes ran through the adapter.
    """
    # Prepare
    # Paths of one length, so cursors past a printed path match too.
    host_workspace = build_trusted_project(tmp_path / "on-host", monkeypatch)
    sandbox_workspace = build_trusted_project(tmp_path / "sandbox", monkeypatch)
    scripted = ScriptedModel({
        _PROCESS_TASK: [("run_typescript", {"code": _PROCESS_PROGRAM})]
    })
    monkeypatch.setattr(EXECUTE_COMPLETION, scripted)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    sandbox = RecordingSandbox(sandbox_workspace)

    # Do
    host_outcome = await _run(host_workspace, None)
    host_messages = _tool_messages(scripted)
    sandbox_outcome = await _run(sandbox_workspace, sandbox)
    sandbox_messages = _tool_messages(scripted)

    # Assert
    assert host_outcome is RunOutcome.FINISHED
    assert sandbox_outcome is RunOutcome.FINISHED
    host_text = _comparable(host_workspace, host_messages)
    [host_result] = host_messages
    assert host_result["outcome"] == "success", host_text
    program = json.loads(host_result["content"][0]["text"])
    assert program["checked"]["seen"].strip() == "gone"
    assert program["treeStopped"]["status"] == "stopped"
    assert _comparable(sandbox_workspace, sandbox_messages) == host_text
    assert "process" in sandbox.helper_operations
    assert _descendants_are_gone(host_workspace)
    assert _descendants_are_gone(sandbox_workspace)
