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


def scaffold_changelog(new_version: str) -> None:
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


def scaffold_whats_new() -> None:
    whats_new_path = Path("vibe/whats_new.md")
    if not whats_new_path.exists():
        raise FileNotFoundError("whats_new.md not found in current directory")

    whats_new_path.write_text("")


def fill_release_notes(current_version: str, new_version: str, autofill: bool) -> None:
    if not autofill:
        print("Skipping CHANGELOG.md and whats_new.md auto-fill.")
        return

    print("Filling CHANGELOG.md and vibe/whats_new.md...")
    prompt = f"""Fill both CHANGELOG.md and vibe/whats_new.md for version {new_version} in a single pass, reusing the same git history context for both files.

Step 1 — Gather context (do this once):
- Inspect git history for commits in origin/main that touch the `vibe` folder since version {current_version}.
- Build a single mental list of relevant changes. Do not mention commit hashes or PR numbers.

Step 2 — Fill CHANGELOG.md:
- Edit the section for version {new_version} that was just scaffolded at the top of the file.
- Follow the existing file convention: Keep a Changelog format with ### Added, ### Changed, ### Fixed, ### Removed. One bullet per line, concise. Match the tone and style of existing entries.
- Remove any subsection that has no bullets (leave no empty ### Added / ### Changed / etc).

Step 3 — Fill vibe/whats_new.md (reuse the same context, do NOT re-inspect git):
- This file is an in-app announcement shown to users on upgrade. It is NOT a changelog. Its only purpose is to advertise a handful of notable new things. Most releases warrant 0-3 bullets. If nothing is genuinely noteworthy to an end user, leave the file empty.
- Inclusion criteria (a bullet must meet ALL):
  * Visible in the CLI/UI: a new command, key binding, screen, flag, or behavior the user will actually notice.
  * Net-new capability or a meaningful UX improvement (not a tweak, polish, or fix unless it unblocks a real workflow).
  * Worth interrupting the user to tell them about.
- Hard exclusions (never include, even if user-facing): bug fixes, small UI polish, copy changes, refactors, performance tweaks, dependency bumps, internal/API-only changes, config plumbing, telemetry, logging, build/CI, tests, docs.
- Be ruthless. When in doubt, leave it out. Prefer an empty file over a weak bullet. Do NOT pad the list to look substantial.
- Format (only if there is at least one qualifying item):
  * First line: "# What's new in v{new_version}" (no other headings).
  * Then up to 3 bullets, one line each: "- **Feature**: short summary" (e.g. "- **Interactive resume**: Added a /resume command to choose which session to resume").
  * Do not copy or paraphrase the full changelog."""
    try:
        result = subprocess.run(
            ["vibe", "-p", prompt, "--auto-approve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise RuntimeError("Failed to auto-fill release notes")
    except Exception:
        print(
            "Warning: failed to auto-fill CHANGELOG.md and whats_new.md, please fill them manually.",
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
    parser.add_argument(
        "--no-autofill",
        action="store_true",
        help="Skip auto-filling CHANGELOG.md and whats_new.md via vibe -p",
    )

    args = parser.parse_args()
    autofill = not args.no_autofill

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

        print()
        scaffold_changelog(new_version=new_version)
        scaffold_whats_new()
        fill_release_notes(
            current_version=current_version, new_version=new_version, autofill=autofill
        )
        print()

        subprocess.run(["uv", "lock"], check=True)

        print(f"\nSuccessfully bumped version from {current_version} to {new_version}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
