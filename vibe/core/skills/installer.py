from pathlib import Path
import subprocess
import shutil


def install_skill(repo_url: str, skill_name: str | None = None, target_dir: Path | None = None) -> bool:
    """Install a skill from a repository.

    Args:
        repo_url: URL of the repository containing the skill.
        skill_name: Name of the skill to install. If None, install all skills.
        target_dir: Target directory to install the skill. Defaults to .vibe/skills/.

    Returns:
        True if installation was successful, False otherwise.
    """
    if target_dir is None:
        target_dir = Path(".vibe/skills")

    try:
        # Clone the repository
        repo_dir = Path(f"temp_{repo_url.split('/')[-1]}")
        subprocess.run(["git", "clone", repo_url, repo_dir], check=True)

        # Locate the skills directory
        skills_dir = repo_dir / "skills"
        if not skills_dir.exists():
            # Try alternative paths for skills
            alternative_paths = [
                repo_dir / "plugins" / "propel-code-review" / "skills",
                repo_dir / "plugins" / "skills",
            ]
            for path in alternative_paths:
                if path.exists():
                    skills_dir = path
                    break
            else:
                return False

        # Copy the skill(s) to the target directory
        if skill_name:
            skill_source = skills_dir / skill_name
            if skill_source.exists():
                shutil.copytree(skill_source, target_dir / skill_name, dirs_exist_ok=True)
        else:
            for skill in skills_dir.iterdir():
                if skill.is_dir():
                    shutil.copytree(skill, target_dir / skill.name, dirs_exist_ok=True)

        # Clean up
        shutil.rmtree(repo_dir)

        return True
    except Exception as e:
        print(f"Error installing skill: {e}")
        return False