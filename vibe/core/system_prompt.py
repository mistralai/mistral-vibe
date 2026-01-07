from __future__ import annotations

from collections.abc import Generator
import fnmatch
import html
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from vibe.core.llm.format import get_active_tool_classes
from vibe.core.paths.config_paths import INSTRUCTIONS_FILE
from vibe.core.prompts import UtilityPrompt
from vibe.core.trusted_folders import TRUSTABLE_FILENAMES, trusted_folders_manager
from vibe.core.utils import is_dangerous_directory, is_windows

if TYPE_CHECKING:
    from vibe.core.config import ProjectContextConfig, VibeConfig
    from vibe.core.skills.manager import SkillManager
    from vibe.core.tools.manager import ToolManager


def _load_user_instructions() -> str:
    try:
        return INSTRUCTIONS_FILE.path.read_text("utf-8", errors="ignore")
    except (FileNotFoundError, OSError):
        return ""


def _load_project_doc(workdir: Path, max_bytes: int) -> str:
    if not trusted_folders_manager.is_trusted(workdir):
        return ""
    for name in TRUSTABLE_FILENAMES:
        path = workdir / name
        try:
            return path.read_text("utf-8", errors="ignore")[:max_bytes]
        except (FileNotFoundError, OSError):
            continue
    return ""


class ProjectContextProvider:
    def __init__(
        self, config: ProjectContextConfig, root_path: str | Path = "."
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.config = config
        self.gitignore_patterns = self._load_gitignore_patterns()
        self._file_count = 0
        self._start_time = 0.0

    def _load_gitignore_patterns(self) -> list[str]:
        gitignore_path = self.root_path / ".gitignore"
        patterns = []

        if gitignore_path.exists():
            try:
                patterns.extend(
                    line.strip()
                    for line in gitignore_path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")
                )
            except Exception as e:
                print(f"Warning: Could not read .gitignore: {e}", file=sys.stderr)

        default_patterns = [
            ".git",
            ".git/*",
            "*.pyc",
            "__pycache__",
            "node_modules",
            "node_modules/*",
            ".env",
            ".DS_Store",
            "*.log",
            ".vscode/settings.json",
            ".idea/*",
            "dist",
            "build",
            "target",
            ".next",
            ".nuxt",
            "coverage",
            ".nyc_output",
            "*.egg-info",
            ".pytest_cache",
            ".tox",
            "vendor",
            "third_party",
            "deps",
            "*.min.js",
            "*.min.css",
            "*.bundle.js",
            "*.chunk.js",
            ".cache",
            "tmp",
            "temp",
            "logs",
        ]

        return patterns + default_patterns

    def _is_ignored(self, path: Path) -> bool:
        try:
            relative_path = path.relative_to(self.root_path)
            path_str = str(relative_path)

            for pattern in self.gitignore_patterns:
                if pattern.endswith("/"):
                    if path.is_dir() and fnmatch.fnmatch(f"{path_str}/", pattern):
                        return True
                elif fnmatch.fnmatch(path_str, pattern):
                    return True
                elif "*" in pattern or "?" in pattern:
                    if fnmatch.fnmatch(path_str, pattern):
                        return True

            return False
        except (ValueError, OSError):
            return True

    def _should_stop(self) -> bool:
        return (
            self._file_count >= self.config.max_files
            or (time.time() - self._start_time) > self.config.timeout_seconds
        )

    def _build_tree_structure_iterative(self) -> Generator[str]:
        self._start_time = time.time()
        self._file_count = 0

        yield from self._process_directory(self.root_path, "", 0, is_root=True)

    def _process_directory(
        self, path: Path, prefix: str, depth: int, is_root: bool = False
    ) -> Generator[str]:
        if depth > self.config.max_depth or self._should_stop():
            return

        try:
            all_items = list(path.iterdir())
            items = [item for item in all_items if not self._is_ignored(item)]

            items.sort(key=lambda p: (not p.is_dir(), p.name.lower()))

            show_truncation = len(items) > self.config.max_dirs_per_level
            if show_truncation:
                items = items[: self.config.max_dirs_per_level]

            for i, item in enumerate(items):
                if self._should_stop():
                    break

                is_last = i == len(items) - 1 and not show_truncation
                connector = "└── " if is_last else "├── "
                name = f"{item.name}{'/' if item.is_dir() else ''}"

                yield f"{prefix}{connector}{name}"
                self._file_count += 1

                if item.is_dir() and depth < self.config.max_depth:
                    child_prefix = prefix + ("    " if is_last else "│   ")
                    yield from self._process_directory(item, child_prefix, depth + 1)

            if show_truncation and not self._should_stop():
                remaining = len(all_items) - len(items)
                yield f"{prefix}└── ... ({remaining} more items)"

        except (PermissionError, OSError):
            pass

    def get_directory_structure(self) -> str:
        lines = []
        header = f"Directory structure of {self.root_path.name} (depth≤{self.config.max_depth}, max {self.config.max_files} items):\n"

        try:
            for line in self._build_tree_structure_iterative():
                lines.append(line)

                current_text = header + "\n".join(lines)
                if (
                    len(current_text)
                    > self.config.max_chars - self.config.truncation_buffer
                ):
                    break

        except Exception as e:
            lines.append(f"Error building structure: {e}")

        structure = header + "\n".join(lines)

        if self._file_count >= self.config.max_files:
            structure += f"\n... (truncated at {self.config.max_files} files limit)"
        elif (time.time() - self._start_time) > self.config.timeout_seconds:
            structure += (
                f"\n... (truncated due to {self.config.timeout_seconds}s timeout)"
            )
        elif len(structure) > self.config.max_chars:
            structure += f"\n... (truncated at {self.config.max_chars} characters)"

        return structure

    def get_git_status(self) -> str:
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
        structure = self.get_directory_structure()
        git_status = self.get_git_status()

        large_repo_warning = ""
        if len(structure) >= self.config.max_chars - self.config.truncation_buffer:
            large_repo_warning = (
                f" Large repository detected - showing summary view with depth limit {self.config.max_depth}. "
                f"Use the LS tool (passing a specific path), Bash tool, and other tools to explore nested directories in detail."
            )

        template = UtilityPrompt.PROJECT_CONTEXT.read()
        return template.format(
            large_repo_warning=large_repo_warning,
            structure=structure,
            abs_path=self.root_path,
            git_status=git_status,
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

    On Unix, this is always 'sh'.
    On Windows, this is COMSPEC or cmd.exe.
    """
    if is_windows():
        return os.environ.get("COMSPEC", "cmd.exe")
    return "sh"


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


def _get_available_skills_section(skill_manager: SkillManager | None) -> str:
    if skill_manager is None:
        return ""

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


def get_universal_system_prompt(
    tool_manager: ToolManager,
    config: VibeConfig,
    skill_manager: SkillManager | None = None,
) -> str:
    sections = [config.system_prompt]

    if config.include_commit_signature:
        sections.append(_add_commit_signature())

    if config.include_model_info:
        sections.append(f"Your model name is: `{config.active_model}`")

    if config.include_prompt_detail:
        sections.append(_get_os_system_prompt())
        tool_prompts = []
        active_tools = get_active_tool_classes(tool_manager, config)
        for tool_class in active_tools:
            if prompt := tool_class.get_tool_prompt():
                tool_prompts.append(prompt)
        if tool_prompts:
            sections.append("\n---\n".join(tool_prompts))

        user_instructions = config.instructions.strip() or _load_user_instructions()
        if user_instructions.strip():
            sections.append(user_instructions)

        skills_section = _get_available_skills_section(skill_manager)
        if skills_section:
            sections.append(skills_section)

    if config.include_project_context:
        is_dangerous, reason = is_dangerous_directory()
        if is_dangerous:
            template = UtilityPrompt.DANGEROUS_DIRECTORY.read()
            context = template.format(
                reason=reason.lower(), abs_path=Path(".").resolve()
            )
        else:
            context = ProjectContextProvider(
                config=config.project_context, root_path=config.effective_workdir
            ).get_full_context()

        sections.append(context)

        project_doc = _load_project_doc(
            config.effective_workdir, config.project_context.max_doc_bytes
        )
        if project_doc.strip():
            sections.append(project_doc)

    return "\n\n".join(sections)
