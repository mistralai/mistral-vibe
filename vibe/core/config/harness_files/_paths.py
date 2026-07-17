from __future__ import annotations

from vibe.core.paths import AGENTS_HOME, VIBE_HOME, GlobalPath

GLOBAL_TOOLS_DIR = GlobalPath(lambda: VIBE_HOME.path / "tools")
GLOBAL_SKILLS_DIR = GlobalPath(lambda: VIBE_HOME.path / "skills")
GLOBAL_REGISTRY_SKILLS_CACHE_DIR = GlobalPath(
    lambda: VIBE_HOME.path / "skills-registry-cache"
)
GLOBAL_AGENTS_DIR = GlobalPath(lambda: VIBE_HOME.path / "agents")
GLOBAL_PROMPTS_DIR = GlobalPath(lambda: VIBE_HOME.path / "prompts")
GLOBAL_AGENTS_SKILLS_DIR = GlobalPath(lambda: AGENTS_HOME.path / "skills")
