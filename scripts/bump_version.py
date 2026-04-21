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


def update_changelog(current_version: str, new_version: str) -> None:
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

    # Auto-fill changelog using Vibe in headless mode
    print("Filling CHANGELOG.md...")
    prompt = f"""Fill the new CHANGELOG.md section for version {new_version} (the one that was just added).

Rules:
- Use only commits that touch the `vibe` folder in this repo since version {current_version}. Inspect git history to list relevant changes.
- Follow the existing file convention: Keep a Changelog format with ### Added, ### Changed, ### Fixed, ### Removed. One bullet per line, concise. Match the tone and style of the entries already in the file.
- Do not mention commit hashes or PR numbers.
- Remove any subsection that has no bullets (leave no empty ### Added / ### Changed / etc)."""
    try:
        result = subprocess.run(
            ["vibe", "-p", prompt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if result.returncode != 0:
            raise RuntimeError("Failed to auto-fill CHANGELOG.md")
    except Exception:
        print(
            "Warning: failed to auto-fill CHANGELOG.md, please fill it manually.",
            file=sys.stderr,
        )


def fill_whats_new_message(new_version: str) -> None:
    whats_new_path = Path("vibe/whats_new.md")
    if not whats_new_path.exists():
        raise FileNotFoundError("whats_new.md not found in current directory")

    whats_new_path.write_text("")

    print("Filling whats_new.md...")
    prompt = f"""Fill vibe/whats_new.md using only the CHANGELOG.md section for version {new_version}.

Rules:
- Include only the most important user-facing changes: visible CLI/UI behavior, new commands or key bindings, UX improvements. Exclude internal refactors, API-only changes, and dev/tooling updates.
- If there are no such changes, write nothing (empty file).
- Otherwise: first line must be "# What's new in v{new_version}" (no extra heading). Then one bullet per item, format: "- **Feature**: short summary" (e.g. - **Interactive resume**: Added a /resume command to choose which session to resume). One line per bullet, concise.
- Do not copy the full changelog; summarize only what matters to someone reading "what's new" in the app."""
    try:
        result = subprocess.run(
            ["vibe", "-p", prompt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if result.returncode != 0:
            raise RuntimeError("Failed to auto-fill whats_new.md")
    except Exception:
        print(
            "Warning: failed to auto-fill whats_new.md, please fill it manually.",
            file=sys.stderr,
        )


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
        print(f"New version: {new_version}\n")

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

        print()
        update_changelog(current_version=current_version, new_version=new_version)

        fill_whats_new_message(new_version=new_version)
        print()

        subprocess.run(["uv", "lock"], check=True)

        print(f"\nSuccessfully bumped version from {current_version} to {new_version}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
