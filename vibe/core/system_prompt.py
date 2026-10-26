from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import contextlib
import os
from pathlib import Path
from string import Template
import subprocess
import threading
from typing import TYPE_CHECKING

from vibe.core.paths import VIBE_HOME
from vibe.core.prompts import UtilityPrompt
from vibe.core.utils import WindowsShellKind, is_windows, resolve_windows_shell
from vibe.utils.paths import to_git_bash_path
from vibe.utils.platform import resolve_git_executable

if TYPE_CHECKING:
    from vibe.core.config import ProjectContextConfig

_git_status_cache: dict[Path, Future[str]] = {}
_git_status_lock = threading.Lock()

GIT_TIMEOUT_MESSAGE = "Git operations timed out (large repository)"
GIT_UNAVAILABLE_MESSAGE = "Not a git repository or git not available"
_KILL_TIMEOUT_SECONDS = 2.0
# The default-branch candidates, local and on origin. Named one by one so git
# reads only these refs, never every ref.
_DEFAULT_BRANCH_REFS = (
    "refs/heads/main",
    "refs/remotes/origin/main",
    "refs/heads/master",
    "refs/remotes/origin/master",
)


def git_bash_form(path: Path | str) -> str | None:
    """The ``/c/...`` spelling of *path* when shell commands run in Git Bash.

    Git Bash spells drives as /c/..., so the prompt gives the model that form
    instead of leaving it to guess between C:\\, C:/ and /c/. Returns None off
    Git Bash, and for UNC paths, which have no drive to convert.
    """
    if not is_windows() or resolve_windows_shell().kind is not WindowsShellKind.BASH:
        return None

    raw = str(path)
    converted = to_git_bash_path(raw)
    return None if converted == raw else converted


def git_metadata_args(num_commits: int) -> tuple[list[str], list[str], list[str]]:
    """The read-only git commands behind the project context.

    Current branch, which of ``main`` and ``master`` exist, locally or on
    ``origin``, and recent commits, in that order. Do not add ``git status``:
    unlike these metadata-only commands, it may pass working-tree contents
    through clean or process filters the repository configures, and project
    context is collected outside the shell permission boundary. Nothing here
    may list or decorate with every ref: that takes seconds on repositories
    with many refs, longer on Windows.
    """
    return (
        ["branch", "--show-current"],
        ["for-each-ref", "--format=%(refname)", *_DEFAULT_BRANCH_REFS],
        # Repository config can select a signature verifier executable.
        # Metadata collection runs before trust and needs only summaries.
        ["log", "--oneline", "--no-decorate", "--no-show-signature", f"-{num_commits}"],
    )


def default_branch(refs: str | None) -> str | None:
    """``main`` or ``master``, whichever the repository has, locally or on origin.

    ``refs`` is full ref names, one per line, as the second command of
    ``git_metadata_args`` prints them. None when the repository has neither,
    or the refs could not be read: naming a branch that does not exist sends
    the agent after it.
    """
    if refs is None:
        return None
    names = {line.strip() for line in refs.splitlines()}
    for candidate in ("main", "master"):
        if (
            f"refs/heads/{candidate}" in names
            or f"refs/remotes/origin/{candidate}" in names
        ):
            return candidate
    return None


def parse_git_log(log_output: str) -> list[str]:
    return [line for raw in log_output.split("\n") if (line := raw.strip())]


def format_git_context(
    current_branch: str, default_branch_refs: str | None, log_output: str
) -> str:
    """Render the git metadata section.

    ``default_branch_refs`` is the output of the second command of
    ``git_metadata_args``, or None on failure.
    """
    git_info_parts = [f"Current branch: {current_branch}"]
    main_branch = default_branch(default_branch_refs)
    if main_branch is not None:
        git_info_parts.append(
            f"Main branch (you will usually use this for PRs): {main_branch}"
        )
    recent_commits = parse_git_log(log_output.strip())
    if recent_commits:
        git_info_parts.append("Recent commits:")
        git_info_parts.extend(recent_commits)
    return "\n".join(git_info_parts)


def render_project_context(abs_path: str, git_status: str) -> str:
    template = UtilityPrompt.PROJECT_CONTEXT.read()
    return Template(template).safe_substitute(abs_path=abs_path, git_status=git_status)


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    # Git for Windows' git.exe is a launcher whose child inherits the output
    # pipes, so killing the launcher alone leaves reads blocked until the child
    # exits. taskkill is addressed absolutely: a bare name would also be
    # searched for in the working directory, which may be an untrusted repo.
    if is_windows():
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [
                    str(system_root / "System32" / "taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_KILL_TIMEOUT_SECONDS,
                check=False,
            )
    process.kill()
    try:
        process.communicate(timeout=_KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.wait()


class ProjectContextProvider:
    def __init__(
        self, config: ProjectContextConfig, root_path: str | Path = "."
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.config = config

    def prefetch_git_status(self) -> None:
        self._git_status(in_background=True)

    def get_git_status(self) -> str:
        return self._git_status(in_background=False).result()

    def _git_status(self, *, in_background: bool) -> Future[str]:
        with _git_status_lock:
            if (status := _git_status_cache.get(self.root_path)) is not None:
                return status
            status = Future[str]()
            _git_status_cache[self.root_path] = status
        if in_background:
            threading.Thread(
                target=self._resolve_git_status,
                args=(status,),
                name="vibe-git-status",
                daemon=True,
            ).start()
        else:
            self._resolve_git_status(status)
        return status

    def _resolve_git_status(self, status: Future[str]) -> None:
        try:
            status.set_result(self._fetch_git_status())
        except BaseException as exc:
            status.set_exception(exc)
            raise

    def _run_git(
        self, args: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        git = resolve_git_executable(cwd=self.root_path)
        if git is None:
            raise FileNotFoundError("No trusted Git executable is available")
        # -c core.fsmonitor= overrides (and disables) any fsmonitor hook a
        # repo's own .git/config declares, for this invocation only. This
        # runs unconditionally on session start, before any trust prompt,
        # so a malicious repo cloned/opened by the user could otherwise use
        # `[core] fsmonitor = <payload>` to get its command executed by
        # `git status`/`git branch`/`git log` here with the user's full
        # privileges. -c on the command line takes precedence over the
        # repo's own config, so this can't be overridden by the repo being
        # inspected.
        command = [git, "-c", "core.fsmonitor=", "--no-optional-locks", *args]
        process = subprocess.Popen(
            command,
            cwd=self.root_path,
            stdin=subprocess.DEVNULL if is_windows() else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, command, stdout, stderr
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _fetch_git_status(self) -> str:
        try:
            timeout = min(self.config.timeout_seconds, 10.0)
            branch_args, remote_args, log_args = git_metadata_args(
                self.config.default_commit_count
            )

            # See ``git_metadata_args`` for why there is no `git status` here.
            with ThreadPoolExecutor(max_workers=3) as pool:
                branch_future = pool.submit(self._run_git, branch_args, timeout)
                remote_future = pool.submit(self._run_git, remote_args, timeout)
                log_future = pool.submit(self._run_git, log_args, timeout)

            current_branch = branch_future.result().stdout.strip()

            default_branch_refs: str | None = None
            try:
                default_branch_refs = remote_future.result().stdout
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass

            return format_git_context(
                current_branch, default_branch_refs, log_future.result().stdout
            )

        except subprocess.TimeoutExpired:
            return GIT_TIMEOUT_MESSAGE
        except subprocess.CalledProcessError:
            return GIT_UNAVAILABLE_MESSAGE
        except Exception as e:
            return f"Error getting git status: {e}"

    def get_full_context(self) -> str:
        abs_path = str(self.root_path)
        if git_bash_path := git_bash_form(abs_path):
            abs_path += (
                f"\nGit Bash path (use this form in shell commands): {git_bash_path}"
            )
        return render_project_context(abs_path, self.get_git_status())


def get_commit_signature_section() -> str:
    return (
        "When you want to commit changes, you will always use the 'git commit' bash command.\n"
        "It will always be suffixed with a line telling it was generated by Mistral Vibe with the appropriate co-authoring information.\n"
        "The format you will always uses is the following heredoc.\n\n"
        "```bash\n"
        "git commit -m <Commit message here>\n\n"
        "Generated by Mistral Vibe.\n"
        "Co-Authored-By: Mistral Vibe <vibe@mistral.ai>\n"
        "```"
    )


def get_agents_md_section(
    user_doc: str,
    project_docs: list[tuple[Path, str]],
    *,
    project_instructions: str | None = None,
) -> str | None:
    """Render user-level and project AGENTS.md docs as one prompt section.

    ``user_doc`` is the user-level doc from ``$VIBE_HOME/AGENTS.md``;
    ``project_docs`` are ``(directory, content)`` pairs ordered outermost-first
    from each open project root up to its trust root. ``project_instructions``,
    given by whoever started the session rather than read from a file, come
    first among the project instructions, so a project doc outranks them as a
    doc closer to the working directory outranks one further out. Returns
    ``None`` when nothing has content, so callers can skip the section
    entirely.
    """
    doc_sections: list[str] = []
    if user_doc.strip():
        doc_sections.append(
            f"## User instructions\n\nContents of {VIBE_HOME.path}/AGENTS.md (user-level instructions):\n\n{user_doc.strip()}"
        )
    given = (project_instructions or "").strip()
    if given:
        doc_sections.append(
            f"## Project instructions\n\nInstructions for this project:\n\n{given}"
        )
    elif project_docs:
        doc_sections.append("## Project instructions (checked into the codebase)")
    for doc_dir, doc_content in project_docs:
        doc_sections.append(
            f"Contents of {doc_dir}/AGENTS.md:\n\n{doc_content.strip()}"
        )
    if not doc_sections:
        return None
    template = UtilityPrompt.AGENTS_DOC.read()
    if given:
        # Instructions the session was started with are meant for its task: the
        # closing hedge that they "may or may not be relevant" would undercut them.
        template = template.split(_AGENTS_DOC_HEDGE, 1)[0].rstrip() + "\n"
    return Template(template).safe_substitute(sections="\n\n".join(doc_sections))


_AGENTS_DOC_HEDGE = "IMPORTANT: this context may or may not be relevant"
