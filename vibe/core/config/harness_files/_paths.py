from __future__ import annotations

from vibe.core.paths import VIBE_HOME, GlobalPath

GLOBAL_TOOLS_DIR = GlobalPath(lambda: VIBE_HOME.path / "tools")
GLOBAL_SKILLS_DIR = GlobalPath(lambda: VIBE_HOME.path / "skills")
GLOBAL_AGENTS_DIR = GlobalPath(lambda: VIBE_HOME.path / "agents")
GLOBAL_PROMPTS_DIR = GlobalPath(lambda: VIBE_HOME.path / "prompts")
GLOBAL_PLUGINS_DIR = GlobalPath(lambda: VIBE_HOME.path / "plugins")
