"""A Sandbox Adapter over a local directory, and a scripted model to drive it."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
from typing import Any, cast

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.vibe import SandboxExecResult
from vibe.core.trusted_folders import trusted_folders_manager

ROOT_DOC = "Root rule: keep answers short."
SUB_DOC = "Sub rule: notes are append-only."
# Set only in the sandbox's command environment, never on the host.
SANDBOX_ONLY_VARIABLE = "VIBE_TEST_SANDBOX_MARK"
# Where a scripted model replaces the provider call.
EXECUTE_COMPLETION = (
    "mistralai_vibe_local_harness.vibe._local_actions.execute_completion"
)

# Stands, in a scripted call's arguments, for the path the last offloaded
# tool result was saved to.
SAVED_OUTPUT_PATH = "<saved-output-path>"
_SAVED_OUTPUT = re.compile(r"saved to the file system under (.+?)\.\\nOutput size")


def find_saved_output_paths(dumped_messages: str) -> list[str]:
    """The paths offloaded tool results were saved to, in order, as the model
    was told them in ``dumped_messages``, a JSON dump of its messages.
    """
    return _SAVED_OUTPUT.findall(dumped_messages)


type Script = list[tuple[str, dict[str, JsonValue]]]


class RecordingSandbox:
    """A Sandbox Adapter over a local directory that records what it is asked.

    A command that outlives its timeout, or ``limit`` when that is shorter, is
    killed with its process group, as swerex kills one.
    """

    def __init__(self, workspace: Path, *, limit: float | None = None) -> None:
        self._workspace = str(workspace)
        self._limit = limit
        self.commands: list[str] = []
        self.reads: list[str] = []

    @property
    def workspace(self) -> str:
        return self._workspace

    @property
    def python(self) -> str:
        return sys.executable

    @property
    def helper_operations(self) -> list[str]:
        operations = []
        for command in self.commands:
            argv = shlex.split(command)
            if "--request-base64" in argv:
                operations.append(argv[argv.index("--request-base64") - 1])
        return operations

    async def execute(
        self, command: str, cwd: str, timeout: float | None
    ) -> SandboxExecResult:
        self.commands.append(command)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env={**os.environ, SANDBOX_ONLY_VARIABLE: "inside"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        if self._limit is not None:
            timeout = self._limit if timeout is None else min(timeout, self._limit)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise
        return SandboxExecResult(
            exit_code=cast(int, process.returncode),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def read_file(self, path: str, max_bytes: int) -> bytes | None:
        self.reads.append(path)
        try:
            with open(path, "rb") as handle:
                return handle.read(max_bytes)
        except OSError:
            return None


def build_trusted_project(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository with a root and a subdirectory AGENTS.md, trusted on the host."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
    monkeypatch.setattr(
        trusted_folders_manager, "find_trust_root", lambda path: path.resolve()
    )
    (root / "sub").mkdir(parents=True)
    (root / "AGENTS.md").write_text(ROOT_DOC, encoding="utf-8")
    (root / "sub" / "AGENTS.md").write_text(SUB_DOC, encoding="utf-8")
    (root / "sub" / "notes.txt").write_text("first note\n", encoding="utf-8")
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    for args in (
        ["init", "-q", "-b", "main"],
        [*identity, "add", "."],
        [*identity, "commit", "-q", "-m", "Add the notes"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


class ScriptedModel:
    """Plays each task's scripted tool calls in order, then answers "done".

    A task is a user message naming one of ``scripts``; a request for no task
    answers at once.
    """

    def __init__(self, scripts: dict[str, Script]) -> None:
        self._scripts = scripts
        self.final_requests: dict[str, list[dict[str, Any]]] = {}
        self.calls = 0

    def final_input(self, task: str) -> str:
        return json.dumps(self.final_requests[task])

    async def __call__(
        self,
        action: RustLLMCallAction,
        messages: list[Any],
        _tools: object,
        _config: object,
        *,
        retry_sink: object | None = None,
        delta_sink: object | None = None,
    ) -> RustCompletionSucceededEvent:
        self.calls += 1
        dumped = [
            message.model_dump(mode="json", by_alias=True) for message in messages
        ]
        task = next(
            (
                task
                for message in dumped
                if message.get("role") == "user"
                for task in self._scripts
                if task in json.dumps(message)
            ),
            None,
        )
        script = self._scripts.get(task or "", [])
        step = sum(1 for message in dumped if message.get("role") == "tool")
        parts: list[RustCompletionResultPart]
        if step < len(script):
            name, arguments = script[step]
            arguments_json = json.dumps(arguments)
            saved = find_saved_output_paths(json.dumps(dumped))
            if saved:
                arguments_json = arguments_json.replace(SAVED_OUTPUT_PATH, saved[-1])
            parts = [
                RustCompletionResultToolCallPart(
                    id=f"{task}-{step}", name=name, arguments_json=arguments_json
                )
            ]
            finish_reason = "tool_call"
        else:
            if task is not None:
                self.final_requests[task] = dumped
            parts = [RustTextContentBlock(text="done")]
            finish_reason = "stop"
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=parts,
                finish_reason=finish_reason,
                usage=RustTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
        )
