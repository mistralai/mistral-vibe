from dataclasses import dataclass
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_SKILLS_DIR = Path(".vibe/skills")
CLONE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    success: bool
    message: str


def _find_skills_dir(repo_dir: Path) -> Path | None:
    for relative_path in (
        Path("skills"),
        Path("plugins/propel-code-review/skills"),
        Path("plugins/skills"),
    ):
        candidate = repo_dir / relative_path
        if candidate.is_dir():
            return candidate
    return None


def _copy_skills(skills_dir: Path, target_dir: Path, skill_name: str | None) -> bool:
    if skill_name is not None:
        # Reject path separators and traversal components to prevent directory traversal
        if '/' in skill_name or '\\' in skill_name or skill_name in ('.', '..'):
            return False
        skill_source = skills_dir / skill_name
        if not skill_source.is_dir():
            return False
        shutil.copytree(skill_source, target_dir / skill_name, dirs_exist_ok=True)
        return True

    copied_any = False
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        shutil.copytree(skill_dir, target_dir / skill_dir.name, dirs_exist_ok=True)
        copied_any = True
    return copied_any


def _stringify_output(output: bytes | str | None) -> str:
    match output:
        case bytes():
            return output.decode("utf-8", errors="replace").strip()
        case str():
            return output.strip()
        case _:
            return ""


def _format_install_error(exc: OSError | subprocess.SubprocessError) -> str:
    match exc:
        case subprocess.TimeoutExpired():
            return (
                f"Repository clone timed out after {CLONE_TIMEOUT_SECONDS} seconds."
            )
        case subprocess.CalledProcessError():
            if details := _stringify_output(exc.stderr) or _stringify_output(exc.stdout):
                return f"Git clone failed: {details}"
            return "Git clone failed."
        case OSError():
            return f"Skill installation failed: {exc}"
        case _:
            return f"Skill installation failed: {exc}"


def _success_message(skill_name: str | None) -> str:
    if skill_name is None:
        return "Skills installed successfully. Restart the application to use them."
    return f"Skill '{skill_name}' installed successfully. Restart the application to use it."


def install_skill(
    repo_url: str, skill_name: str | None = None, target_dir: Path | None = None
) -> SkillInstallResult:
    """Install a skill from a repository.

    Args:
        repo_url: URL of the repository containing the skill.
        skill_name: Name of the skill to install. If None, install all skills.
        target_dir: Target directory to install the skill. Defaults to .vibe/skills/.

    Returns:
        Structured install result with status and user-facing message.
    """
    try:
        target_path = target_dir or DEFAULT_SKILLS_DIR
        target_path.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="vibe-skill-install-") as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            subprocess.run(
                ["git", "clone", repo_url, str(repo_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT_SECONDS,
            )

            if (skills_dir := _find_skills_dir(repo_dir)) is None:
                return SkillInstallResult(
                    success=False,
                    message="No skills directory found in the repository.",
                )

            if not _copy_skills(skills_dir, target_path, skill_name):
                if skill_name is None:
                    return SkillInstallResult(
                        success=False,
                        message="No installable skills were found in the repository.",
                    )
                return SkillInstallResult(
                    success=False,
                    message=f"Skill '{skill_name}' was not found in the repository.",
                )

            return SkillInstallResult(
                success=True,
                message=_success_message(skill_name),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return SkillInstallResult(success=False, message=_format_install_error(exc))
