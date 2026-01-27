#!/usr/bin/env python3
"""Version bumping script for semver versioning.

This script increments the version in pyproject.toml based on the specified bump type:
- major: 1.0.0 -> 2.0.0
- minor: 1.0.0 -> 1.1.0
- micro/patch: 1.0.0 -> 1.0.1
"""

from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal, get_args

BumpType = Literal["major", "minor", "micro", "patch"]
BUMP_TYPES = get_args(BumpType)


def parse_version(version_str: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str.strip())
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")

    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def bump_version(version: str, bump_type: BumpType) -> str:
    major, minor, patch = parse_version(version)

    match bump_type:
        case "major":
            return format_version(major + 1, 0, 0)
        case "minor":
            return format_version(major, minor + 1, 0)
        case "micro" | "patch":
            return format_version(major, minor, patch + 1)


def update_hard_values_files(filepath: str, patterns: list[tuple[str, str]]) -> None:
    path = Path(filepath)

    if not path.exists():
        raise FileNotFoundError(f"{filepath} not found in current directory")

    for pattern, replacement in patterns:
        content = path.read_text()
        updated_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

        if updated_content == content:
            raise ValueError(f"pattern {pattern} not found in {filepath}")

        path.write_text(updated_content)

    print(f"Updated version in {filepath}")


def get_current_version() -> str:
    pyproject_path = Path("pyproject.toml")

    if not pyproject_path.exists():
        raise FileNotFoundError("pyproject.toml not found in current directory")

    content = pyproject_path.read_text()

    version_match = re.search(r'^version = "([^"]+)"$', content, re.MULTILINE)
    if not version_match:
        raise ValueError("Version not found in pyproject.toml")

    return version_match.group(1)


def update_changelog(new_version: str) -> None:
    changelog_path = Path("CHANGELOG.md")

    if not changelog_path.exists():
        raise FileNotFoundError("CHANGELOG.md not found in current directory")

    content = changelog_path.read_text()
    today = date.today().isoformat()

    first_entry_match = re.search(r"^## \[[\d.]+\]", content, re.MULTILINE)
    if not first_entry_match:
        raise ValueError("Could not find version entry in CHANGELOG.md")

    insert_position = first_entry_match.start()

    new_entry = f"## [{new_version}] - {today}\n\n"
    new_entry += "### Added\n\n"
    new_entry += "### Changed\n\n"
    new_entry += "### Fixed\n\n"
    new_entry += "### Removed\n\n"
    new_entry += "\n"

    updated_content = content[:insert_position] + new_entry + content[insert_position:]
    changelog_path.write_text(updated_content)

    print(f"Added changelog entry for version {new_version}")


def print_warning(new_version: str) -> None:
    warning = f"""
{"=" * 80}
⚠️  WARNING: CHANGELOG UPDATE REQUIRED ⚠️
{"=" * 80}

Don't forget to fill in the changelog entry for version {new_version} in CHANGELOG.md! 📝

Also, remember to fill in vibe/whats_new.md if needed (you can leave it blank). 📝

{"=" * 80}
"""
    print(warning, file=sys.stderr)


def clean_up_whats_new_message() -> None:
    whats_new_path = Path("vibe/whats_new.md")
    if not whats_new_path.exists():
        raise FileNotFoundError("whats_new.md not found in current directory")

    whats_new_path.write_text("")


def main() -> None:
    os.chdir(Path(__file__).parent.parent)

    parser = argparse.ArgumentParser(
        description="Bump semver version in pyproject.toml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/bump_version.py major    # 1.0.0 -> 2.0.0
  uv run scripts/bump_version.py minor    # 1.0.0 -> 1.1.0
  uv run scripts/bump_version.py micro    # 1.0.0 -> 1.0.1
  uv run scripts/bump_version.py patch    # 1.0.0 -> 1.0.1
        """,
    )

    parser.add_argument(
        "bump_type", choices=BUMP_TYPES, help="Type of version bump to perform"
    )

    args = parser.parse_args()

    try:
        # Get current version
        current_version = get_current_version()
        print(f"Current version: {current_version}")

        # Calculate new version
        new_version = bump_version(current_version, args.bump_type)
        print(f"New version: {new_version}")

        # Update pyproject.toml
        update_hard_values_files(
            "pyproject.toml",
            [(f'version = "{current_version}"', f'version = "{new_version}"')],
        )
        # Update extension.toml
        update_hard_values_files(
            "distribution/zed/extension.toml",
            [
                (f'version = "{current_version}"', f'version = "{new_version}"'),
                (
                    f"releases/download/v{current_version}",
                    f"releases/download/v{new_version}",
                ),
                (f"-{current_version}.zip", f"-{new_version}.zip"),
            ],
        )
        # Update .vscode/launch.json
        update_hard_values_files(
            ".vscode/launch.json",
            [(f'"version": "{current_version}"', f'"version": "{new_version}"')],
        )
        # Update vibe/core/__init__.py
        update_hard_values_files(
            "vibe/__init__.py",
            [(f'__version__ = "{current_version}"', f'__version__ = "{new_version}"')],
        )
        # Update tests/acp/test_initialize.py
        update_hard_values_files(
            "tests/acp/test_initialize.py",
            [(f'version="{current_version}"', f'version="{new_version}"')],
        )

        # Update CHANGELOG.md
        update_changelog(new_version)

        clean_up_whats_new_message()

        subprocess.run(["uv", "lock"], check=True)

        print(f"\nSuccessfully bumped version from {current_version} to {new_version}")
        print_warning(new_version)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
