from __future__ import annotations

from pathlib import Path

# Where an existing AGENTS.md may live, in preference order.
_AGENTS_MD_LOCATIONS = ("AGENTS.md", ".vibe/AGENTS.md")


async def build_init_prompt(cwd: Path | None = None) -> str:
    """Build the `/init` prompt handed to the agent.

    `/init` is a single agent turn: the agent explores the repo with its own
    tools and authors AGENTS.md itself. There is no separate detection engine —
    the agent reads the manifests, config, and source directly, so the output is
    repo-aware and we don't carry a renderer or a detection registry that rots as
    stacks change. The turn is visible and rewindable like any other.

    Args:
        cwd: Directory to analyze. Defaults to the current working directory.

    Returns:
        A prompt string suitable for driving a single agent turn.
    """
    workdir = (cwd or Path.cwd()).resolve()
    existing = _read_existing_agents_md(workdir)

    if existing.strip():
        action = (
            "An AGENTS.md already exists (shown at the end). Review it against the "
            "current repo and apply concrete, targeted improvements — fix stale "
            "commands, fill real gaps. Do not rewrite it wholesale."
        )
    else:
        action = "No AGENTS.md exists yet. Create one at ./AGENTS.md."

    sections = [
        "Set up this project's AGENTS.md (agent onboarding guide).",
        action,
        (
            "Explore the repo first. Read the manifests (package.json, "
            "pyproject.toml, Cargo.toml, go.mod, composer.json, Gemfile, ...), "
            "build config, CI workflows, and the key source directories to learn "
            "the real stack and commands. For a monorepo, check nested projects, "
            "not just the root. Don't guess — confirm every command and convention "
            "against the actual files before you write it down."
        ),
        (
            "Then author AGENTS.md so a coding agent can be productive immediately:\n"
            "- Build / test / run / lint commands that actually work in THIS repo\n"
            "- Languages, frameworks, and package managers in use\n"
            "- Project layout and where the important code lives\n"
            "- Conventions you can observe in the code (don't invent them)\n"
            "- For a monorepo, note each sub-project's stack and how to work in it\n\n"
            "Keep it concise and accurate — no filler."
        ),
    ]

    if existing.strip():
        sections.append(
            "Existing AGENTS.md:\n```markdown\n" + existing.strip() + "\n```"
        )

    return "\n\n".join(sections)


def _read_existing_agents_md(workdir: Path) -> str:
    for rel in _AGENTS_MD_LOCATIONS:
        path = workdir / rel
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return ""
    return ""
