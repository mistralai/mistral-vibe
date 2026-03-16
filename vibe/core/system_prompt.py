from __future__ import annotations

import html
import os
from pathlib import Path
from string import Template
import subprocess
import sys
from typing import TYPE_CHECKING

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.paths import VIBE_HOME
from vibe.core.prompts import UtilityPrompt
from vibe.core.utils import is_dangerous_directory, is_windows

if TYPE_CHECKING:
    from vibe.core.agents import AgentManager
    from vibe.core.config import ProjectContextConfig, VibeConfig
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

    def _fetch_git_status(self) -> str:
        try:
            timeout = min(self.config.timeout_seconds, 10.0)
            num_commits = self.config.default_commit_count

            current_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                check=True,
                cwd=self.root_path,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=timeout,
            ).stdout.strip()

            main_branch = "main"
            try:
                branches_output = subprocess.run(
                    ["git", "branch", "-r"],
                    capture_output=True,
                    check=True,
                    cwd=self.root_path,
                    stdin=subprocess.DEVNULL if is_windows() else None,
                    text=True,
                    timeout=timeout,
                ).stdout
                if "origin/master" in branches_output:
                    main_branch = "master"
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass

            status_output = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                check=True,
                cwd=self.root_path,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=timeout,
            ).stdout.strip()

            if status_output:
                status_lines = status_output.splitlines()
                MAX_GIT_STATUS_SIZE = 50
                if len(status_lines) > MAX_GIT_STATUS_SIZE:
                    status = (
                        f"({len(status_lines)} changes - use 'git status' for details)"
                    )
                else:
                    status = f"({len(status_lines)} changes)"
            else:
                status = "(clean)"

            log_output = subprocess.run(
                ["git", "log", "--oneline", f"-{num_commits}", "--decorate"],
                capture_output=True,
                check=True,
                cwd=self.root_path,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=timeout,
            ).stdout.strip()

            recent_commits = []
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


def _get_platform_name() -> str:
    platform_names = {
        "win32": "Windows",
        "darwin": "macOS",
        "linux": "Linux",
        "freebsd": "FreeBSD",
        "openbsd": "OpenBSD",
        "netbsd": "NetBSD",
    }
    return platform_names.get(sys.platform, "Unix-like")


def _get_default_shell() -> str:
    """Get the default shell used by asyncio.create_subprocess_shell.

    On Unix, uses $SHELL env var and default to sh.
    On Windows, this is COMSPEC or cmd.exe.
    """
    if is_windows():
        return os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "sh")


def _get_os_system_prompt() -> str:
    shell = _get_default_shell()
    platform_name = _get_platform_name()
    prompt = f"The operating system is {platform_name} with shell `{shell}`"

    if is_windows():
        prompt += "\n" + _get_windows_system_prompt()
    return prompt


def _get_windows_system_prompt() -> str:
    return (
        "### COMMAND COMPATIBILITY RULES (MUST FOLLOW):\n"
        "- DO NOT use Unix commands like `ls`, `grep`, `cat` - they won't work on Windows\n"
        "- Use: `dir` (Windows) for directory listings\n"
        "- Use: backslashes (\\\\) for paths\n"
        "- Check command availability with: `where command` (Windows)\n"
        "- Script shebang: Not applicable on Windows\n"
        "### ALWAYS verify commands work on the detected platform before suggesting them"
    )


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
        "read the full SKILL.md file to load detailed instructions.",
        "",
        "<available_skills>",
    ]

    for name, info in sorted(skills.items()):
        lines.append("  <skill>")
        lines.append(f"    <name>{html.escape(str(name))}</name>")
        lines.append(
            f"    <description>{html.escape(str(info.description))}</description>"
        )
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


def get_universal_system_prompt(
    tool_manager: ToolManager,
    config: VibeConfig,
    skill_manager: SkillManager,
    agent_manager: AgentManager,
) -> str:
    sections = [config.system_prompt]

    if config.include_commit_signature:
        sections.append(_add_commit_signature())

    if config.include_model_info:
        sections.append(f"Your model name is: `{config.active_model}`")

    if config.include_prompt_detail:
        sections.append(_get_os_system_prompt())
        tool_prompts = []
        for tool_class in tool_manager.available_tools.values():
            if prompt := tool_class.get_tool_prompt():
                tool_prompts.append(prompt)
        if tool_prompts:
            sections.append("\n---\n".join(tool_prompts))

        skills_section = _get_available_skills_section(skill_manager)
        if skills_section:
            sections.append(skills_section)

        subagents_section = _get_available_subagents_section(agent_manager)
        if subagents_section:
            sections.append(subagents_section)

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
