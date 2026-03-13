import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_SKILLS_DIR = Path(".vibe/skills")
CLONE_TIMEOUT_SECONDS = 120


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


def install_skill(
    repo_url: str, skill_name: str | None = None, target_dir: Path | None = None
) -> bool:
    """Install a skill from a repository.

    Args:
        repo_url: URL of the repository containing the skill.
        skill_name: Name of the skill to install. If None, install all skills.
        target_dir: Target directory to install the skill. Defaults to .vibe/skills/.

    Returns:
        True if installation was successful, False otherwise.
    """
    target_path = target_dir or DEFAULT_SKILLS_DIR
    target_path.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="vibe-skill-install-") as temp_dir:
            repo_dir = Path(temp_dir) / "repo"
            subprocess.run(
                ["git", "clone", repo_url, str(repo_dir)],
                check=True,
                timeout=CLONE_TIMEOUT_SECONDS,
            )

            if (skills_dir := _find_skills_dir(repo_dir)) is None:
                return False

            return _copy_skills(skills_dir, target_path, skill_name)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Error installing skill: {exc}")
        return False
