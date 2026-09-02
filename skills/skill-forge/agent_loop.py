"""Skill Forge - Agent Loop Integration

This module provides the dispatch hooks for /skill-forge.
It is imported by the skill's SKILL.md instructions.

Flow (as per user's correct architecture):
1. Dispatcher detects /skill-forge (in parse_skill_command or AgentLoop)
2. Calls enter_skill_forge() BEFORE normal skill execution
3. Saves current runtime state (middleware, agent, config) to disk
4. Loads SkillForgeMiddleware + switches to skill-forge-agent
5. Interactive workflow runs with hard gating
6. Artifacts staged to ~/.vibe/skill-forge-state/staging/
7. User asked: "Apply? Save only? Discard? Continue?"
8. Commit: copies staged → live paths, OR restore previous state

STATE FORMAT (disk):
~/.vibe/skill-forge-state/current_session.json
{
  "session_id": "...",
  "started_at": "...",
  "previous_agent_profile": "overseer",
  "previous_middleware_stack": ["TurnLimitMiddleware", "PriceLimitMiddleware"],
  "previous_enabled_skills": ["anti-loop-debug", ...],
  "checkpoint_id": "...",
  "staging_dir": "~/.vibe/skill-forge-state/staging/<session_id>/",
  "saved_dir": "~/.vibe/skill-forge-state/saved/<label>/"
}

STAGING LAYOUT:
~/.vibe/skill-forge-state/staging/<session_id>/
  skills/
  agents/
  prompts/
  middleware/
  validation_report.json
"""
from __future__ import annotations
import sys
from pathlib import Path
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- State storage ---
_saved_pipeline = None
_saved_agent_manager = None
_saved_middlewares: list = []
_saved_agent_name: Optional[str] = None
_saved_agent_config: dict = {}
_saved_middleware_names: list[str] = []
_saved_enabled_skills: list[str] = []
_saved_skill_paths: list[str] = []
_checkpoint_id: Optional[str] = None

_session_id: Optional[str] = None
_staging_dir: Optional[Path] = None
_saved_dir: Optional[Path] = None
_forge_middleware = None
_skill_forge_dir: Optional[Path] = None

STATE_DIR = Path.home() / ".vibe" / "skill-forge-state"
CURRENT_SESSION_FILE = STATE_DIR / "current_session.json"
STAGING_DIR = STATE_DIR / "staging"
SAVED_DIR = STATE_DIR / "saved"


def enter_skill_forge(
    pipeline,
    agent_manager,
    skill_forge_dir: Optional[Path] = None,
    checkpoint_id: Optional[str] = None,
) -> None:
    """Enter skill-forge mode. DISPATCH TRIGGER.

    Call this from:
    - parse_skill_command() when skill_name == 'skill-forge'
    - AgentLoop.act() before _conversation_loop()

    Args:
        pipeline: The middleware pipeline (self.middleware_pipeline)
        agent_manager: The agent manager (self.agent_manager)
        skill_forge_dir: Path to skill-forge skill directory
        checkpoint_id: Current checkpoint/session ID
    """
    global _saved_pipeline, _saved_agent_manager, _saved_middlewares
    global _saved_agent_name, _saved_agent_config, _saved_middleware_names
    global _saved_enabled_skills, _saved_skill_paths, _checkpoint_id
    global _session_id, _staging_dir, _saved_dir
    global _forge_middleware, _skill_forge_dir

    _saved_pipeline = pipeline
    _saved_agent_manager = agent_manager
    _skill_forge_dir = skill_forge_dir
    _checkpoint_id = checkpoint_id

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    SAVED_DIR.mkdir(parents=True, exist_ok=True)

    # Generate session ID
    import uuid
    _session_id = str(uuid.uuid4())
    _staging_dir = STAGING_DIR / _session_id
    _staging_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save middleware stack (names only, not live objects)
    _saved_middlewares = []
    _saved_middleware_names = []
    if hasattr(pipeline, '_middlewares'):
        _saved_middlewares = list(pipeline._middlewares)
        _saved_middleware_names = [m.__class__.__name__ for m in pipeline._middlewares]
        pipeline._middlewares.clear()
    elif hasattr(pipeline, 'middlewares'):
        _saved_middlewares = list(pipeline.middlewares)
        _saved_middleware_names = [m.__class__.__name__ for m in pipeline.middlewares]
        pipeline.middlewares.clear()

    # 2. Save agent loop
    try:
        current_agent = getattr(agent_manager, 'current_agent', None)
        if current_agent:
            _saved_agent_name = getattr(current_agent, 'name', 'unknown')
            _saved_agent_config = getattr(current_agent, 'config', {})
    except Exception as e:
        logger.warning("Could not save agent: %s", e)
        _saved_agent_name = None

    # 3. Save skill/config state
    try:
        config = agent_manager.config
        _saved_enabled_skills = getattr(config, 'enabled_skills', [])
        _saved_skill_paths = [str(p) for p in getattr(config, 'skill_paths', [])]
    except Exception as e:
        logger.warning("Could not save config state: %s", e)

    # 4. Persist to disk
    state = {
        "session_id": _session_id,
        "started_at": json.dumps({"time": None})[1:-1],  # placeholder
        "previous_agent_profile": _saved_agent_name,
        "previous_middleware_stack": _saved_middleware_names,
        "previous_enabled_skills": _saved_enabled_skills,
        "previous_skill_paths": _saved_skill_paths,
        "checkpoint_id": _checkpoint_id,
        "staging_dir": str(_staging_dir),
        "saved_dir": str(SAVED_DIR),
    }
    CURRENT_SESSION_FILE.write_text(json.dumps(state, indent=2))

    # 5. Add skill-forge dir to sys.path
    if skill_forge_dir and str(skill_forge_dir) not in sys.path:
        sys.path.insert(0, str(skill_forge_dir))

    # 6. Load SkillForgeMiddleware
    try:
        from middleware import SkillForgeMiddleware
        _forge_middleware = SkillForgeMiddleware()
        pipeline.add(_forge_middleware)
        logger.info("Loaded SkillForgeMiddleware")
    except Exception as e:
        logger.error("Failed to load SkillForgeMiddleware: %s", e)
        _forge_middleware = None

    # 7. Switch to skill-forge agent
    try:
        agent_manager.switch_profile('skill-forge')
        logger.info("Switched to skill-forge agent loop")
    except Exception as e:
        logger.warning("Could not switch to skill-forge agent: %s", e)

    print(
        f"[SkillForge] Saved state (session: {_session_id}). "
        f"Entered Skill Forge mode with interactive gating. "
        f"Staging dir: {_staging_dir}"
    )


def stage_artifact(artifact_type: str, name: str, content: str) -> Path:
    """Write an artifact to staging area.

    Args:
        artifact_type: 'skills', 'agents', 'prompts', 'middleware', 'workflows'
        name: filename or directory name
        content: file content

    Returns:
        Path to the staged file
    """
    if _staging_dir is None:
        raise RuntimeError("Not in skill-forge mode")

    target_dir = _staging_dir / artifact_type
    target_dir.mkdir(parents=True, exist_ok=True)

    # Handle directory-based artifacts (skills)
    if artifact_type == 'skills':
        skill_dir = target_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        file_path = skill_dir / "SKILL.md"
    else:
        file_path = target_dir / name

    file_path.write_text(content)
    logger.info("Staged artifact: %s", file_path)
    return file_path


def validate_staged_artifacts() -> dict:
    """Validate all staged artifacts.

    Returns:
        dict with 'valid' (bool), 'errors' (list), 'warnings' (list)
    """
    if _staging_dir is None:
        return {"valid": False, "errors": ["Not in skill-forge mode"], "warnings": []}

    errors = []
    warnings = []

    # Check skills
    skills_dir = _staging_dir / "skills"
    if skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                errors.append(f"Missing SKILL.md in {skill_dir.name}")
                continue
            # Try to parse frontmatter
            try:
                from vibe.core.skills.parser import parse_skill_markdown
                content = skill_md.read_text()
                parse_skill_markdown(content)
            except Exception as e:
                errors.append(f"Invalid SKILL.md in {skill_dir.name}: {e}")

    # Save validation report
    report = {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
    report_file = _staging_dir / "validation_report.json"
    report_file.write_text(json.dumps(report, indent=2))

    return report


def commit_staged_artifacts() -> None:
    """Copy staged artifacts to live Vibe paths.

    Only call this if user says YES to "Apply?".
    """
    if _staging_dir is None:
        raise RuntimeError("Not in skill-forge mode")

    vibe_dir = Path.home() / ".vibe"

    # Copy skills
    src_skills = _staging_dir / "skills"
    if src_skills.is_dir():
        dst_skills = vibe_dir / "skills"
        dst_skills.mkdir(parents=True, exist_ok=True)
        for skill_dir in src_skills.iterdir():
            if skill_dir.is_dir():
                dst = dst_skills / skill_dir.name
                import shutil
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(skill_dir, dst)
                logger.info("Committed skill: %s", skill_dir.name)

    # Copy agents
    src_agents = _staging_dir / "agents"
    if src_agents.is_dir():
        dst_agents = vibe_dir / "agents"
        dst_agents.mkdir(parents=True, exist_ok=True)
        for agent_file in src_agents.iterdir():
            if agent_file.is_file():
                dst = dst_agents / agent_file.name
                import shutil
                shutil.copy2(agent_file, dst)
                logger.info("Committed agent: %s", agent_file.name)

    # Copy prompts
    src_prompts = _staging_dir / "prompts"
    if src_prompts.is_dir():
        dst_prompts = vibe_dir / "prompts"
        dst_prompts.mkdir(parents=True, exist_ok=True)
        for prompt_file in src_prompts.iterdir():
            if prompt_file.is_file():
                dst = dst_prompts / prompt_file.name
                import shutil
                shutil.copy2(prompt_file, dst)
                logger.info("Committed prompt: %s", prompt_file.name)

    print(f"[SkillForge] Committed staged artifacts to {vibe_dir}")


def save_for_reuse(label: str) -> Path:
    """Save current staged work for later reuse (without committing).

    Args:
        label: Label for this saved state

    Returns:
        Path to the saved directory
    """
    if _staging_dir is None:
        raise RuntimeError("Not in skill-forge mode")

    import shutil
    dst = SAVED_DIR / label
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(_staging_dir, dst)
    logger.info("Saved staged work to %s", dst)
    return dst


def exit_skill_forge(apply_user_work: bool = True) -> None:
    """Exit skill-forge mode. RESTORE TRIGGER.

    This should be called in a `finally` block to guarantee restoration.

    Args:
        apply_user_work: If True, commit staged → then restore.
                            If False, discard staged → restore.
    """
    global _saved_pipeline, _saved_agent_manager, _saved_middlewares
    global _saved_agent_name, _saved_middleware_names
    global _session_id, _staging_dir, _saved_dir
    global _forge_middleware, _skill_forge_dir

    try:
        # 1. Commit or discard
        if apply_user_work:
            print("[SkillForge] Applying work, then restoring previous state.")
            try:
                commit_staged_artifacts()
            except Exception as e:
                logger.error("Failed to commit artifacts: %s", e)
        else:
            print("[SkillForge] Discarding work, restoring previous state.")

        # 2. Remove SkillForgeMiddleware
        if _forge_middleware and _saved_pipeline:
            if hasattr(_saved_pipeline, '_middlewares'):
                _saved_pipeline._middlewares = [
                    m for m in _saved_pipeline._middlewares
                    if m is not _forge_middleware
                ]
            elif hasattr(_saved_pipeline, 'middlewares'):
                _saved_pipeline.middlewares = [
                    m for m in _saved_pipeline.middlewares
                    if m is not _forge_middleware
                ]
            logger.info("Removed SkillForgeMiddleware")

        # 3. Restore middleware
        if _saved_pipeline and _saved_middlewares:
            if hasattr(_saved_pipeline, '_middlewares'):
                _saved_pipeline._middlewares.extend(_saved_middlewares)
            elif hasattr(_saved_pipeline, 'middlewares'):
                _saved_pipeline.middlewares.extend(_saved_middlewares)
            logger.info("Restored %d middlewares", len(_saved_middlewares))

        # 4. Restore agent loop
        if _saved_agent_manager and _saved_agent_name:
            try:
                _saved_agent_manager.switch_profile(_saved_agent_name)
                logger.info("Restored agent loop: %s", _saved_agent_name)
            except Exception as e:
                logger.error("Failed to restore agent loop: %s", e)

        # 5. Clean up sys.path
        if _skill_forge_dir:
            skill_dir_str = str(_skill_forge_dir)
            if skill_dir_str in sys.path:
                sys.path.remove(skill_dir_str)

        print("[SkillForge] Exited Skill Forge mode. Previous state restored.")

    finally:
        # 6. Clean up state
        if CURRENT_SESSION_FILE.exists():
            CURRENT_SESSION_FILE.unlink()

        # Reset globals
        _saved_pipeline = None
        _saved_agent_manager = None
        _saved_middlewares = []
        _saved_agent_name = None
        _saved_agent_config = {}
        _saved_middleware_names = []
        _saved_enabled_skills = []
        _saved_skill_paths = []
        _checkpoint_id = None
        _session_id = None
        _staging_dir = None
        _saved_dir = None
        _forge_middleware = None
        _skill_forge_dir = None
