from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import html
import os
from pathlib import Path
from string import Template
import subprocess
from typing import TYPE_CHECKING

from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.experiments import ExperimentName
from vibe.core.logger import logger
from vibe.core.paths import VIBE_HOME
from vibe.core.prompts import MissingPromptFileError, UtilityPrompt, load_system_prompt
from vibe.core.utils import (
    WindowsShellKind,
    get_platform_display_name,
    is_dangerous_directory,
    is_windows,
    resolve_windows_shell,
)

if TYPE_CHECKING:
    from vibe.core.agents import AgentManager
    from vibe.core.config import ProjectContextConfig
    from vibe.core.experiments import ExperimentManager
    from vibe.core.skills.manager import SkillManager
    from vibe.core.tools.manager import ToolManager

_git_status_cache: dict[Path, str] = {}


class ProjectContextProvider:
    def __init__(
        self, config: ProjectContextConfig, root_path: str | Path = "."
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.config = config

    def get_git_status(self) -> str:
        if self.root_path in _git_status_cache:
            return _git_status_cache[self.root_path]

        result = self._fetch_git_status()
        _git_status_cache[self.root_path] = result
        return result

    def _run_git(
        self, args: list[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "--no-optional-locks", *args],
            capture_output=True,
            check=True,
            cwd=self.root_path,
            stdin=subprocess.DEVNULL if is_windows() else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    @staticmethod
    def _format_git_status(status_output: str) -> str:
        if not status_output:
            return "(clean)"
        status_lines = status_output.splitlines()
        MAX_GIT_STATUS_SIZE = 50
        if len(status_lines) > MAX_GIT_STATUS_SIZE:
            return f"({len(status_lines)} changes - use 'git status' for details)"
        return f"({len(status_lines)} changes)"

    @staticmethod
    def _parse_git_log(log_output: str) -> list[str]:
        recent_commits: list[str] = []
        for line in log_output.split("\n"):
            if not (line := line.strip()):
                continue
            if " " in line:
                commit_hash, commit_msg = line.split(" ", 1)
                if (
                    "(" in commit_msg
                    and ")" in commit_msg
                    and (paren_index := commit_msg.rfind("(")) > 0
                ):
                    commit_msg = commit_msg[:paren_index].strip()
                recent_commits.append(f"{commit_hash} {commit_msg}")
            else:
                recent_commits.append(line)
        return recent_commits

    def _fetch_git_status(self) -> str:
        try:
            timeout = min(self.config.timeout_seconds, 10.0)
            num_commits = self.config.default_commit_count

            with ThreadPoolExecutor(max_workers=4) as pool:
                branch_future = pool.submit(
                    self._run_git, ["branch", "--show-current"], timeout
                )
                remote_future = pool.submit(self._run_git, ["branch", "-r"], timeout)
                status_future = pool.submit(
                    self._run_git, ["status", "--porcelain"], timeout
                )
                log_future = pool.submit(
                    self._run_git,
                    ["log", "--oneline", f"-{num_commits}", "--decorate"],
                    timeout,
                )

            current_branch = branch_future.result().stdout.strip()

            main_branch = "main"
            try:
                branches_output = remote_future.result().stdout
                if "origin/master" in branches_output:
                    main_branch = "master"
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass

            status = self._format_git_status(status_future.result().stdout.strip())
            recent_commits = self._parse_git_log(log_future.result().stdout.strip())

            git_info_parts = [
                f"Current branch: {current_branch}",
                f"Main branch (you will usually use this for PRs): {main_branch}",
                f"Status: {status}",
            ]

            if recent_commits:
                git_info_parts.append("Recent commits:")
                git_info_parts.extend(recent_commits)

            return "\n".join(git_info_parts)

        except subprocess.TimeoutExpired:
            return "Git operations timed out (large repository)"
        except subprocess.CalledProcessError:
            return "Not a git repository or git not available"
        except Exception as e:
            return f"Error getting git status: {e}"

    def get_full_context(self) -> str:
        git_status = self.get_git_status()

        template = UtilityPrompt.PROJECT_CONTEXT.read()
        return Template(template).safe_substitute(
            abs_path=str(self.root_path), git_status=git_status
        )


def _get_os_system_prompt() -> str:
    platform_name = get_platform_display_name()

    if not is_windows():
        shell = os.environ.get("SHELL", "sh")
        return f"The operating system is {platform_name} with shell `{shell}`"

    shell = resolve_windows_shell()
    if shell.kind is WindowsShellKind.BASH and shell.executable is not None:
        shell_display = f"bash ({shell.executable})"
    else:
        shell_display = shell.executable or "cmd.exe"

    prompt = f"The operating system is {platform_name} with shell `{shell_display}`"
    prompt += "\n" + _get_windows_system_prompt(shell.kind)
    return prompt


def _format_current_date() -> str:
    today = date.today()
    return f"{today.isoformat()} ({today.strftime('%A')})"


def _get_windows_bash_system_prompt() -> str:
    return (
        "### COMMAND COMPATIBILITY RULES (MUST FOLLOW):\n"
        "- Commands run through bash (Git Bash), so Unix commands like `ls`, "
        "`grep`, `cat`, `find` work - this is NOT cmd.exe or PowerShell\n"
        "- Discard output with `2>/dev/null` - NEVER `2>nul` or `2>$null`\n"
        "- `&&` and `||` are valid for command chaining\n"
        "- Prefer forward slashes in paths; bash resolves Windows drives as "
        "`/c/Users/...`\n"
        "- Check command availability with: `command -v <command>`\n"
        "### ALWAYS verify commands work on the detected platform before suggesting them"
    )


def _get_windows_cmd_system_prompt() -> str:
    return (
        "### COMMAND COMPATIBILITY RULES (MUST FOLLOW):\n"
        "- The shell is cmd.exe, NOT bash or PowerShell\n"
        "- DO NOT use Unix commands like `ls`, `grep`, `cat` - they won't work; "
        "use `dir`, `findstr`, `type`\n"
        "- Use backslashes (\\\\) for paths\n"
        "- Discard output with `2>nul` - NEVER `2>/dev/null` or `2>$null`\n"
        "- `&&` and `||` are valid for command chaining in cmd.exe\n"
        "- Check command availability with: `where command`\n"
        "- Script shebang: Not applicable on Windows\n"
        "### ALWAYS verify commands work on the detected platform before suggesting them"
    )


def _get_windows_system_prompt(shell_kind: WindowsShellKind) -> str:
    if shell_kind is WindowsShellKind.BASH:
        return _get_windows_bash_system_prompt()
    return _get_windows_cmd_system_prompt()


def _add_commit_signature() -> str:
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


def _get_available_skills_section(skill_manager: SkillManager) -> str:
    skills = skill_manager.available_skills
    if not skills:
        return ""

    lines = [
        "# Available Skills",
        "",
        "You have access to the following skills. When a task matches a skill's description,",
        "use the `skill` tool if available to load the full skill instructions, if it is not available, read the files manually if they exist.",
        "",
        "When a user message is exactly `/skill-name` (optionally followed by extra",
        "instructions), the user has explicitly invoked that skill. Its instructions are",
        "loaded for you automatically: you will see a `skill` tool call and result",
        "immediately after that message. Treat the loaded content as the active",
        "instructions and act on it — you do not need to call the `skill` tool yourself.",
        "",
        "<available_skills>",
    ]

    for name, info in sorted(skills.items()):
        lines.append("  <skill>")
        lines.append(f"    <name>{html.escape(str(name))}</name>")
        lines.append(
            f"    <description>{html.escape(str(info.description))}</description>"
        )
        if info.skill_path is not None:
            lines.append(f"    <path>{html.escape(str(info.skill_path))}</path>")
        lines.append("  </skill>")

    lines.append("</available_skills>")

    return "\n".join(lines)


def _get_available_subagents_section(agent_manager: AgentManager) -> str:
    agents = agent_manager.get_subagents()
    if not agents:
        return ""

    lines = ["# Available Subagents", ""]
    lines.append("The following subagents can be spawned via the Task tool:")
    for agent in agents:
        lines.append(f"- **{agent.name}**: {agent.description}")

    return "\n".join(lines)


def _get_scratchpad_section(scratchpad_dir: Path | None) -> str | None:
    if not scratchpad_dir:
        return None
    return (
        "# Scratchpad Directory\n\n"
        f"You have a scratchpad directory at: `{scratchpad_dir}`\n\n"
        "Use this for temporary files: intermediate results, draft scripts, "
        "working files, outputs that don't belong in the project.\n"
        "Files here are automatically allowed — no permission prompts.\n"
        "Session-scoped. Shared with subagents."
    )


def _resolve_system_prompt(
    config: VibeConfigSchema, experiment_manager: ExperimentManager | None
) -> str:
    default_prompt_id = VibeConfigSchema.model_fields["system_prompt_id"].default
    if config.system_prompt_id != default_prompt_id:
        logger.info(
            "System prompt loaded: id=%s (user config overrides experiments)",
            config.system_prompt_id,
        )
        return config.system_prompt

    prompt_id = (
        experiment_manager.get_variant_or_none(ExperimentName.SYSTEM_PROMPT)
        if experiment_manager is not None
        else None
    )

    if prompt_id is None:
        logger.info(
            "System prompt loaded: id=%s (user config)", config.system_prompt_id
        )
        return config.system_prompt

    try:
        prompt = load_system_prompt(prompt_id)
    except MissingPromptFileError:
        logger.warning(
            "System prompt loaded: id=%s (variant '%s' missing, fell back)",
            config.system_prompt_id,
            prompt_id,
        )
        return config.system_prompt
    logger.info("System prompt loaded: id=%s (experiment variant)", prompt_id)
    return prompt


def _interpolate_prompt(prompt: str) -> str:
    return Template(prompt).safe_substitute(current_date=_format_current_date())


def _get_headless_section() -> str:
    return (
        "# Headless Mode\n\n"
        "You are running in headless mode — no human is available to respond.\n"
        "Do not ask questions, request confirmation, or wait for user input.\n"
        "If the task is ambiguous, make the best judgment call and proceed.\n"
        "Complete the entire task in a single pass. Produce a final, complete result.\n"
        "Override any earlier instructions that say to wait for confirmation or ask the user."
    )


def get_universal_system_prompt(
    tool_manager: ToolManager,
    config: VibeConfigSchema,
    skill_manager: SkillManager,
    agent_manager: AgentManager,
    *,
    scratchpad_dir: Path | None = None,
    headless: bool = False,
    experiment_manager: ExperimentManager | None = None,
) -> str:
    sections = [_interpolate_prompt(_resolve_system_prompt(config, experiment_manager))]

    if headless:
        sections.append(_get_headless_section())

    if config.include_commit_signature:
        sections.append(_add_commit_signature())

    if config.include_model_info:
        sections.append(f"Your model name is: `{config.active_model}`")

    if config.include_prompt_detail:
        sections.append(_get_os_system_prompt())

        skills_section = _get_available_skills_section(skill_manager)
        if skills_section:
            sections.append(skills_section)

        subagents_section = _get_available_subagents_section(agent_manager)
        if subagents_section:
            sections.append(subagents_section)

        sections.extend(filter(None, [_get_scratchpad_section(scratchpad_dir)]))

    if config.include_project_context:
        is_dangerous, reason = is_dangerous_directory()
        if is_dangerous:
            template = UtilityPrompt.DANGEROUS_DIRECTORY.read()
            context = Template(template).safe_substitute(
                reason=reason.lower(), abs_path=Path(".").resolve()
            )
        else:
            context = ProjectContextProvider(
                config=config.project_context, root_path=Path.cwd()
            ).get_full_context()

        sections.append(context)

        mgr = get_harness_files_manager()
        cwd_resolved = Path.cwd().resolve()
        extra_roots = [r for r in mgr.project_roots if r.resolve() != cwd_resolved]
        if extra_roots:
            dirs_lines = "\n".join(f" - {d}" for d in extra_roots)
            sections.append(
                "Additional working directories (treated with the same "
                "file-access permissions as the primary working directory):\n"
                + dirs_lines
            )

        user_doc = mgr.load_user_doc()
        project_docs = mgr.load_project_docs()

        doc_sections: list[str] = []
        if user_doc.strip():
            doc_sections.append(
                f"## User instructions\n\nContents of {VIBE_HOME.path}/AGENTS.md (user-level instructions):\n\n{user_doc.strip()}"
            )
        if project_docs:
            doc_sections.append("## Project instructions (checked into the codebase)")
        for doc_dir, doc_content in project_docs:
            doc_sections.append(
                f"Contents of {doc_dir}/AGENTS.md:\n\n{doc_content.strip()}"
            )
        if doc_sections:
            template = UtilityPrompt.AGENTS_DOC.read()
            sections.append(
                Template(template).safe_substitute(sections="\n\n".join(doc_sections))
            )

    return "\n\n".join(sections)
