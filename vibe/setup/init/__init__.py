from __future__ import annotations

from pathlib import Path

from vibe.setup.init.analyzer import CodebaseAnalysis, analyze_codebase

# Where an existing AGENTS.md may live, in preference order.
_AGENTS_MD_LOCATIONS = ("AGENTS.md", ".vibe/AGENTS.md")


async def build_init_prompt(cwd: Path | None = None) -> str:
    """Build the `/init` prompt handed to the agent.

    Runs a cheap deterministic scan for high-signal facts (build/test commands,
    languages, frameworks, sub-projects, ...) and wraps them in instructions that
    tell the agent to verify those facts against the repo and author AGENTS.md
    itself. The agent — not a template — writes the file, so the output is
    repo-aware and we don't carry a renderer that rots as stacks change.

    Args:
        cwd: Directory to analyze. Defaults to the current working directory.

    Returns:
        A prompt string suitable for driving a single agent turn.
    """
    workdir = (cwd or Path.cwd()).resolve()
    analysis = await analyze_codebase(workdir)
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
            "A quick deterministic scan found the facts below. Treat them as hints "
            "only — they can be incomplete or wrong. Verify each command and "
            "convention against the actual manifests, config, and source before "
            "relying on it."
        ),
        _format_facts(analysis),
        (
            "Author AGENTS.md so a coding agent can be productive immediately:\n"
            "- Build / test / run / lint commands that actually work in THIS repo\n"
            "- Project layout and where the important code lives\n"
            "- Conventions you can observe in the code (don't invent them)\n"
            "- For a monorepo, note each sub-project's stack and how to work in it\n\n"
            "Read the key files to confirm before writing. Keep it concise and "
            "accurate — no filler."
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


def _format_facts(analysis: CodebaseAnalysis) -> str:
    """Render the high-signal scan results as a compact bullet list."""
    lines: list[str] = []

    def add(label: str, value: object) -> None:
        if isinstance(value, list):
            if value:
                lines.append(f"- {label}: {', '.join(value)}")
        elif value:
            lines.append(f"- {label}: {value}")

    add("Project", analysis.project_name)
    add("Description", analysis.project_description)
    add("Version", analysis.project_version)
    add("Languages", analysis.languages)
    add("Frameworks", analysis.frameworks)
    add("Package managers", analysis.package_managers)
    add("Build commands", analysis.build_commands)
    add("Test commands", analysis.test_commands)
    add("Run commands", analysis.run_commands)
    add("Lint commands", analysis.lint_commands)
    add("Dev environments", analysis.dev_environments)
    add("Monorepo tooling", analysis.monorepo_tools)
    add("Sub-projects", analysis.subprojects)
    add("Source dirs", analysis.source_dirs)
    add("Test dirs", analysis.test_dirs)

    if not lines:
        return "Scan facts: (none detected — analyze the repo directly)."
    return "Scan facts:\n" + "\n".join(lines)
