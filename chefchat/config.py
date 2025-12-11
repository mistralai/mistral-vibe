"""ChefChat Palate Configuration System.

Load user preferences from `.chef/palate.toml`.
Provides sensible defaults if config doesn't exist.

Usage:
    from chefchat.config import load_palate_config
    config = load_palate_config()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class HealingConfig:
    """Self-healing loop configuration."""

    max_attempts: int = 3
    timeout_seconds: int = 60


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    provider: str = "openai"  # openai | anthropic
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 4096


@dataclass
class PalateConfig:
    """ChefChat configuration loaded from .chef/palate.toml.

    Attributes:
        framework: Test framework to use (pytest, unittest)
        linter: Linter to use (ruff, flake8, pylint)
        strictness: Quality level (michelin, bistro, fast_food)
        healing: Self-healing loop settings
        llm: LLM provider settings
    """

    framework: str = "pytest"
    linter: str = "ruff"
    strictness: str = "michelin"
    healing: HealingConfig = field(default_factory=HealingConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PalateConfig:
        """Create config from dictionary (parsed TOML).

        Args:
            data: Parsed TOML dictionary

        Returns:
            PalateConfig instance
        """
        kitchen = data.get("kitchen", {})
        healing_data = data.get("healing", {})
        llm_data = data.get("llm", {})

        return cls(
            framework=kitchen.get("framework", "pytest"),
            linter=kitchen.get("linter", "ruff"),
            strictness=kitchen.get("strictness", "michelin"),
            healing=HealingConfig(
                max_attempts=healing_data.get("max_attempts", 3),
                timeout_seconds=healing_data.get("timeout_seconds", 60),
            ),
            llm=LLMConfig(
                provider=llm_data.get("provider", "openai"),
                model=llm_data.get("model", "gpt-4"),
                temperature=llm_data.get("temperature", 0.7),
                max_tokens=llm_data.get("max_tokens", 4096),
            ),
        )


def load_palate_config(project_root: str | Path | None = None) -> PalateConfig:
    """Load configuration from .chef/palate.toml.

    Falls back to defaults if file doesn't exist.

    Args:
        project_root: Project root directory (defaults to cwd)

    Returns:
        PalateConfig with loaded or default values
    """
    root = Path(project_root) if project_root else Path.cwd()
    config_path = root / ".chef" / "palate.toml"

    if not config_path.exists():
        return PalateConfig()

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return PalateConfig.from_dict(data)
    except Exception:
        # Fall back to defaults on parse error
        return PalateConfig()


def get_test_command(config: PalateConfig, target_path: str = ".") -> list[str]:
    """Get the test command based on config.

    Args:
        config: Palate configuration
        target_path: Path to test

    Returns:
        Command list for subprocess
    """
    if config.framework == "pytest":
        cmd = ["uv", "run", "pytest", target_path, "-v", "--tb=short"]
        if config.strictness == "michelin":
            cmd.extend(["--strict-markers", "-W", "error"])
        elif config.strictness == "fast_food":
            cmd.append("-q")
    else:
        cmd = ["uv", "run", "python", "-m", "unittest", "discover", target_path]

    return cmd


def get_lint_command(config: PalateConfig, target_path: str = ".") -> list[str]:
    """Get the lint command based on config.

    Args:
        config: Palate configuration
        target_path: Path to lint

    Returns:
        Command list for subprocess
    """
    if config.linter == "ruff":
        cmd = ["uv", "run", "ruff", "check", target_path]
        if config.strictness == "michelin":
            cmd.append("--select=ALL")
    elif config.linter == "flake8":
        cmd = ["uv", "run", "flake8", target_path]
    else:
        cmd = ["uv", "run", "pylint", target_path]

    return cmd
