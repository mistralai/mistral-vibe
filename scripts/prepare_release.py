#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


def run_git_command(
    *args: str, check: bool = True, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    result = subprocess.run(
        ["git"] + list(args), check=check, capture_output=capture_output, text=True
    )
    return result


def ensure_public_remote() -> None:
    result = run_git_command("remote", "-v", capture_output=True, check=False)
    remotes = result.stdout

    public_remote_url = "git@github.com:mistralai/mistral-vibe.git"
    if public_remote_url in remotes:
        print("Public remote already exists with correct URL")
        return

    print(f"Creating public remote: {public_remote_url}")
    run_git_command("remote", "add", "public", public_remote_url)
    print("Public remote created successfully")


def switch_to_tag(version: str) -> None:
    tag = f"v{version}"
    print(f"Switching to tag {tag}...")

    result = run_git_command(
        "rev-parse", "--verify", tag, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"Tag {tag} does not exist")

    run_git_command("switch", "--detach", tag)
    print(f"Successfully switched to tag {tag}")


def get_version_from_pyproject() -> str:
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        raise FileNotFoundError("pyproject.toml not found in current directory")

    content = pyproject_path.read_text()
    version_match = re.search(r'^version = "([^"]+)"$', content, re.MULTILINE)
    if not version_match:
        raise ValueError("Version not found in pyproject.toml")

    return version_match.group(1)


def get_latest_version() -> str:
    result = run_git_command("ls-remote", "--tags", "public", capture_output=True)
    remote_tags_output = (
        result.stdout.strip().split("\n") if result.stdout.strip() else []
    )

    if not remote_tags_output:
        raise ValueError("No version tags found on public remote")

    versions: list[tuple[int, int, int, str]] = []
    MIN_PARTS_IN_LS_REMOTE_LINE = 2  # hash and ref
    for line in remote_tags_output:
        parts = line.split()
        if len(parts) < MIN_PARTS_IN_LS_REMOTE_LINE:
            continue

        _hash, tag_ref = parts[0], parts[1]
        if not tag_ref.startswith("refs/tags/"):
            continue

        tag = tag_ref.replace("refs/tags/", "")
        match = re.match(r"^v(\d+\.\d+\.\d+)$", tag)
        if not match:
            continue

        tag_version = match.group(1)
        try:
            major, minor, patch = parse_version(tag_version)
            versions.append((major, minor, patch, tag_version))
        except ValueError:
            continue

    if not versions:
        raise ValueError(
            "No valid version tags found on public remote (format: vX.Y.Z)"
        )

    versions.sort()

    return max(versions)[3]


def parse_version(version_str: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version_str.strip())
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")

    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def create_release_branch(version: str) -> None:
    branch_name = f"release/v{version}"
    print(f"Creating release branch: {branch_name}")

    result = run_git_command(
        "branch", "--list", branch_name, capture_output=True, check=False
    )
    if result.stdout.strip():
        print(f"Warning: Branch {branch_name} already exists", file=sys.stderr)
        response = input(f"Delete and recreate {branch_name}? (y/N): ")
        if response.lower() == "y":
            run_git_command("branch", "-D", branch_name)
        else:
            print("Aborting", file=sys.stderr)
            sys.exit(1)

    run_git_command("switch", "-c", branch_name)
    print(f"Created and switched to branch {branch_name}")


def cherry_pick_commits(previous_version: str, current_version: str) -> None:
    previous_tag = f"v{previous_version}-private"
    current_tag = f"v{current_version}-private"

    result = run_git_command(
        "rev-parse", "--verify", previous_tag, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"Tag {previous_tag} does not exist")

    result = run_git_command(
        "rev-parse", "--verify", current_tag, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"Tag {current_tag} does not exist")

    print(f"Cherry-picking commits from {previous_tag}..{current_tag}...")
    run_git_command("cherry-pick", f"{previous_tag}..{current_tag}")
    print("Successfully cherry-picked all commits")


def get_commits_summary(previous_version: str, current_version: str) -> str:
    previous_tag = f"v{previous_version}-private"
    current_tag = f"v{current_version}-private"

    result = run_git_command(
        "log", f"{previous_tag}..{current_tag}", "--oneline", capture_output=True
    )
    return result.stdout.strip()


def get_changelog_entry(version: str) -> str:
    changelog_path = Path("CHANGELOG.md")
    if not changelog_path.exists():
        return "CHANGELOG.md not found"

    content = changelog_path.read_text()

    pattern = rf"^## \[{re.escape(version)}\] - .+?(?=^## \[|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)

    if not match:
        return f"No changelog entry found for version {version}"

    return match.group(0).strip()


def print_summary(
    current_version: str,
    previous_version: str,
    commits_summary: str,
    changelog_entry: str,
) -> None:
    print("\n" + "=" * 80)
    print("RELEASE PREPARATION SUMMARY")
    print("=" * 80)
    print(f"\nVersion: {current_version}")
    print(f"Previous version: {previous_version}")
    print(f"Release branch: release/v{current_version}")

    print("\n" + "-" * 80)
    print("COMMITS IN THIS RELEASE")
    print("-" * 80)
    if commits_summary:
        print(commits_summary)
    else:
        print("No commits found")

    print("\n" + "-" * 80)
    print("CHANGELOG ENTRY")
    print("-" * 80)
    print(changelog_entry)

    print("\n" + "-" * 80)
    print("NEXT STEPS")
    print("-" * 80)
    print(
        f"To review/edit commits before publishing, use interactive rebase:\n"
        f"  git rebase -i v{previous_version}"
    )

    print("\n" + "-" * 80)
    print("REMINDERS")
    print("-" * 80)
    print("Before publishing the release:")
    print("  ✓ Credit any public contributors in the release notes")
    print("  ✓ Close related issues once the release is published")
    print(
        "  ✓ Review and update the changelog if needed "
        "(should be made in the private main branch)"
    )
    print("\n" + "=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a release branch by cherry-picking from private tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("version", help="Version to prepare release for (e.g., 1.1.3)")

    args = parser.parse_args()
    current_version = args.version

    try:
        # Step 1: Ensure public remote exists
        ensure_public_remote()

        # Step 2: Fetch all remotes
        print("Fetching all remotes...")
        run_git_command("fetch", "--all")
        print("Successfully fetched all remotes")

        # Step 3: Find latest version
        previous_version = get_latest_version()
        print(f"Previous version: {previous_version}")

        # Step 4: Verify version matches pyproject.toml
        pyproject_version = get_version_from_pyproject()
        if current_version != pyproject_version:
            raise ValueError(
                f"Version mismatch: provided version '{current_version}' does not match "
                f"pyproject.toml version '{pyproject_version}'"
            )
        print(f"Version verified: {current_version}")

        # Step 5: Switch to previous version tag
        switch_to_tag(previous_version)

        # Step 6: Create release branch
        create_release_branch(current_version)

        # Step 7: Cherry-pick commits
        cherry_pick_commits(previous_version, current_version)

        # Step 8: Get summary information
        commits_summary = get_commits_summary(previous_version, current_version)
        changelog_entry = get_changelog_entry(current_version)

        # Step 9: Print summary
        print_summary(
            current_version, previous_version, commits_summary, changelog_entry
        )

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
