from __future__ import annotations

from pathlib import Path
import tomllib

from packaging.requirements import Requirement

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _miniaudio_requirement() -> Requirement:
    dependencies = tomllib.loads(_PYPROJECT.read_text())["project"]["dependencies"]
    requirements = [Requirement(dependency) for dependency in dependencies]
    return next(
        requirement for requirement in requirements if requirement.name == "miniaudio"
    )


def test_miniaudio_is_an_unconditional_default_dependency() -> None:
    # Upstream publishes wheels for every supported platform (macOS, Windows
    # including ARM64, Linux x86_64 and aarch64, musl and glibc), so miniaudio
    # must stay a plain base dependency: platform markers would silently drop
    # voice features on the excluded platforms.
    assert _miniaudio_requirement().marker is None
