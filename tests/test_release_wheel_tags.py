from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"
_PLATFORMS = [
    "manylinux_2_28_x86_64",
    "manylinux_2_28_aarch64",
    "macosx_11_0_x86_64",
    "macosx_11_0_arm64",
    "win_amd64",
]


@pytest.mark.parametrize(
    "replacement,accepted",
    [
        (None, True),
        ("cp312-abi3-linux_x86_64", False),
        ("cp312-abi3-manylinux_2_35_x86_64", False),
        ("cp312-cp312-manylinux_2_28_x86_64", False),
        ("py3-none-any", False),
    ],
)
def test_release_rejects_unexpected_wheel_tags(
    tmp_path: Path, replacement: str | None, accepted: bool
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    for index, platform in enumerate(_PLATFORMS):
        tag = replacement if index == 0 and replacement else f"cp312-abi3-{platform}"
        (dist / f"mistral_vibe-1.0.0-{tag}.whl").touch()
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    steps = workflow["jobs"]["release-pypi"]["steps"]
    validation = next(
        (step["run"] for step in steps if step["name"] == "List distributions"), None
    )
    assert validation is not None, "List distributions step missing from release.yml"

    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", validation],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert (result.returncode == 0) == accepted, result.stdout + result.stderr
