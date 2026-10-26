from __future__ import annotations

from pathlib import Path
import tomllib

from packaging.requirements import Requirement
import pytest

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _miniaudio_requirement() -> Requirement:
    dependencies = tomllib.loads(_PYPROJECT.read_text())["project"]["dependencies"]
    requirements = [Requirement(dependency) for dependency in dependencies]
    return next(
        requirement for requirement in requirements if requirement.name == "miniaudio"
    )


@pytest.mark.parametrize(
    "sys_platform,platform_system,platform_machine,installed",
    [
        ("darwin", "Darwin", "arm64", True),
        ("darwin", "Darwin", "x86_64", True),
        ("linux", "Linux", "x86_64", True),
        ("win32", "Windows", "AMD64", True),
        ("win32", "Windows", "ARM64", True),
        ("linux", "Linux", "aarch64", False),
        ("linux", "Linux", "armv7l", False),
        ("openbsd7", "OpenBSD", "amd64", False),
        ("freebsd14", "FreeBSD", "amd64", False),
    ],
)
def test_miniaudio_is_a_default_dependency_on_macos_windows_and_x86_64_linux_only(
    sys_platform: str, platform_system: str, platform_machine: str, installed: bool
) -> None:
    marker = _miniaudio_requirement().marker
    assert marker is not None

    environment = {
        "sys_platform": sys_platform,
        "platform_system": platform_system,
        "platform_machine": platform_machine,
    }

    assert marker.evaluate(environment) is installed
