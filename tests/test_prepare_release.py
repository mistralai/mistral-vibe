from __future__ import annotations

import subprocess
import tomllib

from scripts import prepare_release


def test_pin_dependencies_preserves_toml_format_and_refreshes_lock(
    monkeypatch, tmp_path
):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """[project]
name = "mistral-vibe"
version = "2.9.6"
license = { text = "Apache-2.0" }
authors = [{ name = "Mistral AI" }]
dependencies = [
    "httpx[http2]>=0.28.1",
]

[dependency-groups]
build = ["pyinstaller>=6.17.0"]

[tool.pytest.ini_options]
filterwarnings = [
    # Keep this comment when pinning dependencies.
    "ignore:example",
]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_get_pinned_dependencies(group=None):
        if group == "build":
            return ["pyinstaller==6.17.0", "truststore==0.10.4"]
        return ["anyio==4.12.0", 'httpx[http2]==0.28.1 ; python_version >= "3.12"']

    uv_commands = []

    def fake_subprocess_run(args, **kwargs):
        uv_commands.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

    git_commands = []

    def fake_run_git_command(*args, **kwargs):
        git_commands.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

    monkeypatch.setattr(
        prepare_release, "get_pinned_dependencies", fake_get_pinned_dependencies
    )
    monkeypatch.setattr(prepare_release.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(prepare_release, "run_git_command", fake_run_git_command)

    prepare_release.pin_dependencies("2.9.6")

    updated_pyproject = pyproject_path.read_text(encoding="utf-8")
    updated_data = tomllib.loads(updated_pyproject)
    assert 'license = { text = "Apache-2.0" }' in updated_pyproject
    assert 'authors = [{ name = "Mistral AI" }]' in updated_pyproject
    assert "# Keep this comment when pinning dependencies." in updated_pyproject
    assert updated_data["project"]["dependencies"] == [
        "anyio==4.12.0",
        'httpx[http2]==0.28.1 ; python_version >= "3.12"',
    ]
    assert updated_data["dependency-groups"]["build"] == [
        "pyinstaller==6.17.0",
        "truststore==0.10.4",
    ]
    assert uv_commands == [["uv", "lock"]]
    assert ("add", "pyproject.toml", "uv.lock") in git_commands
    assert (
        "commit",
        "--allow-empty",
        "-m",
        "chore: pin dependencies for v2.9.6",
    ) in git_commands
