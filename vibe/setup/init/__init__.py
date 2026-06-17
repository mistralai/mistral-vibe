from __future__ import annotations

from pathlib import Path

from vibe.setup.init.analyzer import analyze_codebase
from vibe.setup.init.generator import GenerationMode, generate_agents_md


async def run_init(
    cwd: Path | None = None,
    interactive: bool = False,
    artifacts: list[str] | None = None,
) -> str:
    """Run the init workflow to generate AGENTS.md files.
    
    Args:
        cwd: The working directory to analyze. Defaults to current directory.
        interactive: Whether to run in interactive mode (VIBE_CODE_NEW_INIT=1)
        artifacts: List of artifacts to set up (AGENTS.md files, skills, hooks)
        
    Returns:
        Result message describing what was done.
    """
    workdir = cwd or Path.cwd()
    
    if interactive:
        return await _run_interactive_init(workdir, artifacts)
    else:
        return await _run_standard_init(workdir)


async def _run_standard_init(workdir: Path) -> str:
    """Run standard init: analyze codebase and generate/improve AGENTS.md."""
    # Check if AGENTS.md already exists
    agents_md_path = workdir / "AGENTS.md"
    agents_md_in_vibe = workdir / ".vibe" / "AGENTS.md"
    
    existing_content = ""
    if agents_md_path.exists():
        existing_content = agents_md_path.read_text(encoding="utf-8")
    elif agents_md_in_vibe.exists():
        existing_content = agents_md_in_vibe.read_text(encoding="utf-8")
    
    # Analyze the codebase
    analysis = await analyze_codebase(workdir)
    
    if existing_content.strip():
        # Suggest improvements to existing file
        suggestions = generate_agents_md(analysis, mode=GenerationMode.SUGGEST, existing_content=existing_content)
        return suggestions
    else:
        # Generate new AGENTS.md
        content = generate_agents_md(analysis, mode=GenerationMode.CREATE)
        
        # Prefer ./AGENTS.md over ./.vibe/AGENTS.md
        target_path = agents_md_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        target_path.write_text(content, encoding="utf-8")
        return f"Created AGENTS.md at {target_path.relative_to(workdir)} with project conventions and workflows."


async def _run_interactive_init(workdir: Path, artifacts: list[str] | None) -> str:
    """Run interactive init with multi-phase flow."""
    # TODO: Implement interactive multi-phase flow
    # This would:
    # 1. Ask which artifacts to set up (AGENTS.md files, skills, hooks)
    # 2. Explore codebase with subagent
    # 3. Fill in gaps via follow-up questions
    # 4. Present reviewable proposal before writing files
    
    # For now, fall back to standard init
    return await _run_standard_init(workdir)
