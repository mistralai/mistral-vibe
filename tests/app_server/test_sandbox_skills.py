"""Skills of sessions whose tools run behind a Sandbox Adapter.

The adapter here runs every command in a real subprocess, with the sandbox's
temporary directory pointed at a directory of the test's own, so what a run
copies into the sandbox is on disk to inspect.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from tests.stubs.local_sandbox import (
    EXECUTE_COMPLETION,
    RecordingSandbox,
    ScriptedModel,
    build_trusted_project,
)
from vibe.app_server._sandbox_skills import SKILLS_DIRNAME, SandboxSkills
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
from vibe.core.skills.models import SkillInfo

pytestmark = [
    # Whole sessions with subprocess tools: slower than a unit test under load.
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="the sandbox runs POSIX command lines"
    ),
]

_TASK = "SKILL_TASK"
_BUILTIN_SKILLS = Path(__file__).parents[2] / "vibe/plugins/builtins/vibe/skills"
# Only the commands that write a copy carry these: the helper's install
# operation, and the file a large request is streamed into.
_INSTALL_MARKER = " skills-install "
_UPLOAD_MARKERS = (_INSTALL_MARKER, "mistralai-vibe-request-")
_BASE_DIR = re.compile(r"Base directory for this skill: ([^\\\n]+)")
_SKILL_PATH = re.compile(r"<path>([^<]+/SKILL\.md)</path>")


@pytest.fixture
def sandbox_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The sandbox's temporary directory, where skills are copied."""
    path = tmp_path / "sandbox-tmp"
    path.mkdir()
    monkeypatch.setenv("TMPDIR", str(path))
    return path


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build_trusted_project(tmp_path / "project", monkeypatch)


def _options(sandbox: RecordingSandbox) -> LocalHarnessOptions:
    return LocalHarnessOptions(
        client=ClientDescriptor(
            info=ClientInfo(name="vibe_test", title="Vibe test", version="0"),
            capabilities=ClientCapabilities(callback_kinds=["approval", "user_input"]),
        ),
        session_options=SessionOptions(
            agent=BuiltinAgentName.AUTO_APPROVE, headless=True
        ),
        experimental_harness=True,
        sandbox=sandbox,
    )


async def _run_skill(
    project: Path, monkeypatch: pytest.MonkeyPatch, skill: str
) -> tuple[RecordingSandbox, str]:
    """Run a session whose model loads ``skill``; return its sandbox and input."""
    model = ScriptedModel({_TASK: [("skill", {"name": skill})]})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    sandbox = RecordingSandbox(project)
    host = LocalHarnessHost()
    report = await run_headless(
        harness_options=_options(sandbox),
        prompt=_TASK,
        stop=StopRequests(RunLimits()),
        harness_host=host,
    )
    await host.close()
    assert report.result.outcome is RunOutcome.FINISHED, report.result.error
    return sandbox, model.final_input(_TASK)


def _uploads(sandbox: RecordingSandbox) -> list[str]:
    return [
        command
        for command in sandbox.commands
        if any(marker in command for marker in _UPLOAD_MARKERS)
    ]


def _installs(sandbox: RecordingSandbox) -> list[str]:
    """The commands that write copies, as opposed to streaming their input."""
    return [command for command in sandbox.commands if _INSTALL_MARKER in command]


def _copies(sandbox_tmp: Path) -> dict[str, dict[str, bytes]]:
    """Each copy in the sandbox, by digest: its files by relative path."""
    root = sandbox_tmp / SKILLS_DIRNAME
    if not root.is_dir():
        return {}
    return {
        copy.name: {
            path.relative_to(copy).as_posix(): path.read_bytes()
            for path in sorted(copy.rglob("*"))
            if path.is_file()
        }
        for copy in root.iterdir()
    }


def _write_skill(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return directory


def _workspace_changes(project: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.asyncio
async def test_the_builtin_skills_are_copied_once_and_reused_by_a_later_run(
    project: Path, sandbox_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A sandbox with nothing copied yet.
    *Do*: Run a session that loads the built-in `vibe:vibe` skill, then a
    second one on the same sandbox.
    *Assert*: The first run uploads once, and its copies hold the built-in
    skills' files; the model is told sandbox paths for every skill and for the
    loaded one's base directory; the second run uploads nothing and is told
    the same paths; neither touches the workspace.
    """
    # Do
    first, first_input = await _run_skill(project, monkeypatch, "vibe:vibe")
    second, second_input = await _run_skill(project, monkeypatch, "vibe:vibe")

    # Assert
    copies = _copies(sandbox_tmp)
    builtins = {
        directory.name: (directory / "SKILL.md").read_bytes()
        for directory in _BUILTIN_SKILLS.iterdir()
        if (directory / "SKILL.md").is_file()
    }
    assert sorted(copy["SKILL.md"] for copy in copies.values()) == sorted(
        builtins.values()
    )
    assert all(list(copy) == ["SKILL.md"] for copy in copies.values())
    assert len(_installs(first)) == 1
    assert _uploads(second) == []

    root = f"{sandbox_tmp}/{SKILLS_DIRNAME}/"
    paths = _SKILL_PATH.findall(first_input)
    assert len(paths) == len(builtins)
    assert all(path.startswith(root) for path in paths)
    assert {path.removeprefix(root).removesuffix("/SKILL.md") for path in paths} == (
        set(copies)
    )
    vibe_digest = next(
        digest
        for digest, copy in copies.items()
        if copy["SKILL.md"] == builtins["vibe"]
    )
    assert _BASE_DIR.findall(first_input) == [f"{root}{vibe_digest}"]
    assert _SKILL_PATH.findall(second_input) == paths
    assert _BASE_DIR.findall(second_input) == [f"{root}{vibe_digest}"]
    assert str(_BUILTIN_SKILLS) not in first_input
    assert _workspace_changes(project) == ""


@pytest.mark.asyncio
async def test_a_user_skill_is_copied_with_its_files(
    project: Path, sandbox_tmp: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A user skill with a script, and a cache directory the skill
    tool skips.
    *Do*: Run a session that loads it.
    *Assert*: Its copy holds the skill and its script, not the cache; the
    model is told the copy's directory and the script, never the host path;
    the workspace is untouched.
    """
    # Prepare
    skill_dir = _write_skill(config_dir / "skills" / "deploy", "deploy", "Deploy.")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.sh").write_text("echo deploy\n", encoding="utf-8")
    (skill_dir / "__pycache__").mkdir()
    (skill_dir / "__pycache__" / "x.pyc").write_bytes(b"cache")

    # Do
    _, model_input = await _run_skill(project, monkeypatch, "deploy")

    # Assert
    (digest,) = [
        digest
        for digest, copy in _copies(sandbox_tmp).items()
        if b"Deploy." in copy["SKILL.md"]
    ]
    copy = _copies(sandbox_tmp)[digest]
    assert sorted(copy) == ["SKILL.md", "scripts/run.sh"]
    base_dir = f"{sandbox_tmp}/{SKILLS_DIRNAME}/{digest}"
    assert _BASE_DIR.findall(model_input) == [base_dir]
    assert f"<path>{base_dir}/SKILL.md</path>" in model_input
    assert "<file>scripts/run.sh</file>" in model_input
    assert str(skill_dir) not in model_input
    assert _workspace_changes(project) == ""


@pytest.mark.asyncio
async def test_a_project_skill_is_read_in_the_sandbox_and_wins_over_a_user_skill(
    project: Path, sandbox_tmp: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A project skill in the sandbox's `.agents/skills`, with a
    checklist, and a user skill of the same name.
    *Do*: Run a session that loads it.
    *Assert*: The model gets the project's skill at its sandbox path, with
    its checklist, and nothing of it is copied.
    """
    # Prepare
    skill_dir = _write_skill(
        project / ".agents" / "skills" / "release", "release", "Project release."
    )
    (skill_dir / "checklist.md").write_text("- tag\n", encoding="utf-8")
    _write_skill(config_dir / "skills" / "release", "release", "User release.")

    # Do
    _, model_input = await _run_skill(project, monkeypatch, "release")

    # Assert
    assert "Project release." in model_input
    assert "User release." not in model_input
    assert _BASE_DIR.findall(model_input) == [str(skill_dir)]
    assert f"<path>{skill_dir}/SKILL.md</path>" in model_input
    assert "<file>checklist.md</file>" in model_input
    assert not any(
        b"Project release." in copy["SKILL.md"]
        for copy in _copies(sandbox_tmp).values()
    )


@pytest.mark.asyncio
async def test_a_sandbox_without_skills_of_its_own_needs_no_upload(
    project: Path, sandbox_tmp: Path
) -> None:
    """*Prepare*: No skill to copy, and a project without skills.
    *Do*: Prepare a session.
    *Assert*: One scan, no upload, and no project skill.
    """
    # Prepare
    sandbox = RecordingSandbox(project)

    # Do
    skills = await SandboxSkills(sandbox).prepare_session([], roots=[project])

    # Assert
    assert skills.project_skills == {}
    assert skills.issues == ()
    assert len(sandbox.commands) == 1
    assert _uploads(sandbox) == []


def _host_skill(directory: Path, name: str, *, files: dict[str, bytes]) -> SkillInfo:
    _write_skill(directory, name, f"The {name} body.")
    for relative, content in files.items():
        (directory / relative).parent.mkdir(parents=True, exist_ok=True)
        (directory / relative).write_bytes(content)
    return SkillInfo(
        name=name,
        description=f"The {name} skill.",
        prompt=f"The {name} body.",
        skill_path=directory / "SKILL.md",
    )


@pytest.mark.asyncio
async def test_a_broken_copy_is_replaced_and_an_intact_one_reused(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    """*Prepare*: A skill copied by one process, whose copy is then altered.
    *Do*: Prepare it from a new process, then from a third.
    *Assert*: The second uploads it again and restores the copy; the third
    reuses it.
    """
    # Prepare
    skill = _host_skill(tmp_path / "host" / "notes", "notes", files={"a.txt": b"a"})
    await SandboxSkills(RecordingSandbox(project)).prepare_session([skill], roots=[])
    (digest,) = _copies(sandbox_tmp)
    copy = sandbox_tmp / SKILLS_DIRNAME / digest
    (copy / "a.txt").write_bytes(b"tampered")
    restoring, reusing = RecordingSandbox(project), RecordingSandbox(project)

    # Do
    await SandboxSkills(restoring).prepare_session([skill], roots=[])
    reused = SandboxSkills(reusing)
    await reused.prepare_session([skill], roots=[])

    # Assert
    assert len(_uploads(restoring)) == 1
    assert (copy / "a.txt").read_bytes() == b"a"
    assert _uploads(reusing) == []
    location = reused.locate(skill)
    assert location is not None
    assert location.path == f"{copy}/SKILL.md"
    assert location.files == ("a.txt",)


@pytest.mark.asyncio
async def test_a_skill_too_large_for_one_argument_is_staged_and_copied(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    """*Prepare*: A skill whose upload exceeds one command line argument.
    *Do*: Prepare it.
    *Assert*: It is uploaded in several commands, and copied intact.
    """
    # Prepare
    asset = os.urandom(200 * 1024)
    skill = _host_skill(tmp_path / "host" / "big", "big", files={"data.bin": asset})
    sandbox = RecordingSandbox(project)

    # Do
    skills = SandboxSkills(sandbox)
    await skills.prepare_session([skill], roots=[])

    # Assert
    assert len(_uploads(sandbox)) > 2
    assert skills.locate(skill) is not None
    ((_, copy),) = _copies(sandbox_tmp).items()
    assert copy["data.bin"] == asset
    assert not [path for path in sandbox_tmp.iterdir() if path.is_file()]


@pytest.mark.asyncio
async def test_a_skill_over_the_size_limits_is_left_out(
    tmp_path: Path, project: Path, sandbox_tmp: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A skill with a file over the per-file limit, and a small one.
    *Do*: Prepare both.
    *Assert*: Only the small one is copied; the other is left out, with a
    config issue that names it.
    """
    # Prepare
    large = _host_skill(
        tmp_path / "host" / "large", "large", files={"a.bin": b"x" * (257 * 1024)}
    )
    small = _host_skill(tmp_path / "host" / "small", "small", files={})
    skills = SandboxSkills(RecordingSandbox(project))

    # Do
    with caplog.at_level(logging.WARNING):
        session = await skills.prepare_session([large, small], roots=[])

    # Assert
    assert skills.locate(large) is None
    assert skills.locate(small) is not None
    assert len(_copies(sandbox_tmp)) == 1
    (issue,) = session.issues
    assert issue.file == str(large.skill_path)
    assert "a.bin exceeds the size limit" in issue.message
    assert any(str(large.skill_path) in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_skills_directory_others_can_write_is_not_used(
    tmp_path: Path, project: Path, sandbox_tmp: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A skills directory in the sandbox that every user can write.
    *Do*: Prepare a skill.
    *Assert*: Nothing is copied there, and the skill is left out, with a
    config issue that says why.
    """
    # Prepare
    root = sandbox_tmp / SKILLS_DIRNAME
    root.mkdir()
    root.chmod(0o777)
    skill = _host_skill(tmp_path / "host" / "notes", "notes", files={})
    skills = SandboxSkills(RecordingSandbox(project))

    # Do
    with caplog.at_level(logging.WARNING):
        session = await skills.prepare_session([skill], roots=[])

    # Assert
    assert skills.locate(skill) is None
    assert list(root.iterdir()) == []
    (issue,) = session.issues
    assert issue.file == str(skill.skill_path)
    assert "other users can reach" in issue.message
    assert any("other users can reach" in r.getMessage() for r in caplog.records)
