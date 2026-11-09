from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

from git import Git, Repo
from git.exc import GitCommandError
import pytest

import vibe.core.git.fetch as fetch_module
from vibe.core.git.fetch import UnsafeGitFetchError, fetch_remote, prepare_secure_fetch


def _repo_with_remote(tmp_path: Path, url: str) -> Repo:
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value('remote "origin"', "url", url).release()
    return repo


def _marker_command(tmp_path: Path, name: str) -> tuple[Path, str]:
    marker = tmp_path / name
    script = tmp_path / f"{name}.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    )
    return marker, shlex.join([sys.executable, str(script)])


def _write_test_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str | bytes
) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    config = home / ".gitconfig"
    if isinstance(contents, bytes):
        config.write_bytes(contents)
    else:
        config.write_text(contents)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)
    return config


def _config_from_env(env: dict[str, str]) -> dict[str, str]:
    return {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }


def _process_env(env: dict[str, str]) -> dict[str, str]:
    process_env = os.environ.copy()
    process_env.update(env)
    return process_env


def _write_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _write_marker_executable(path: Path, marker: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 0\n")
    path.chmod(0o755)
    return path


def _run_credential_fill(repo: Repo, env: dict[str, str]) -> None:
    executable = Git.GIT_PYTHON_GIT_EXECUTABLE
    assert executable is not None
    subprocess.run(
        [executable, "-C", repo.working_dir, "credential", "fill"],
        input="protocol=https\nhost=example.invalid\n\n",
        capture_output=True,
        check=False,
        env=_process_env(env),
        text=True,
        timeout=5,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX command quoting")
@pytest.mark.parametrize(
    ("section", "option", "shell_helper"),
    [
        ("credential", "helper", True),
        ('credential "https://example.invalid"', "helper", True),
        ("core", "askPass", False),
    ],
)
def test_repository_credential_commands_are_not_executed(
    tmp_path: Path, section: str, option: str, shell_helper: bool
) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.invalid/repo.git")
    marker, command = _marker_command(tmp_path, option.lower())
    value = f"!{command}" if shell_helper else command
    repo.config_writer().set_value(section, option, value).release()

    secure = prepare_secure_fetch(repo, "origin")
    _run_credential_fill(repo, secure.env)

    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX command quoting")
@pytest.mark.parametrize("variable", ["GIT_ASKPASS", "SSH_ASKPASS"])
def test_inherited_askpass_commands_are_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.invalid/repo.git")
    marker, command = _marker_command(tmp_path, variable.lower())
    monkeypatch.setenv(variable, command)

    secure = prepare_secure_fetch(repo, "origin")
    _run_credential_fill(repo, secure.env)

    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX command quoting")
def test_trusted_global_credential_helper_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker, command = _marker_command(tmp_path, "trusted-credential-helper")
    _write_test_global_config(
        tmp_path, monkeypatch, f"[credential]\n\thelper = !{command}\n"
    )
    repo = _repo_with_remote(tmp_path / "checkout", "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")
    _run_credential_fill(repo, secure.env)

    assert marker.exists()


def test_trusted_global_safe_directory_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    configured_checkout = checkout.as_posix()
    _write_test_global_config(
        tmp_path, monkeypatch, f"[safe]\n\tdirectory = {configured_checkout}\n"
    )
    repo = _repo_with_remote(checkout, "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")
    process_env = _process_env(secure.env)
    process_env["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
    executable = Git.GIT_PYTHON_GIT_EXECUTABLE
    assert executable is not None
    status = subprocess.run(
        [executable, "-C", repo.working_dir, "status", "--porcelain"],
        capture_output=True,
        check=False,
        env=process_env,
        text=True,
        timeout=5,
    )

    assert status.returncode == 0, status.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX helper script")
def test_named_credential_helper_cannot_resolve_from_checkout_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    marker = tmp_path / "checkout-helper-ran"
    _write_marker_executable(project / "git-credential-planted", marker)
    _write_test_global_config(
        tmp_path, monkeypatch, "[credential]\n\thelper = planted\n"
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv(
        "PATH", os.pathsep.join((".", str(project), os.environ.get("PATH", "")))
    )
    repo = _repo_with_remote(project, "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")
    _run_credential_fill(repo, secure.env)

    assert "." not in secure.env["PATH"].split(os.pathsep)
    assert str(project) not in secure.env["PATH"].split(os.pathsep)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX helper script")
def test_named_credential_helper_from_trusted_path_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    trusted_bin = tmp_path / "trusted-bin"
    marker = tmp_path / "trusted-helper-ran"
    _write_marker_executable(trusted_bin / "git-credential-trusted", marker)
    _write_test_global_config(
        tmp_path, monkeypatch, "[credential]\n\thelper = trusted\n"
    )
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(trusted_bin), os.environ.get("PATH", "")))
    )
    repo = _repo_with_remote(project, "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")
    _run_credential_fill(repo, secure.env)

    assert str(trusted_bin.resolve()) in secure.env["PATH"].split(os.pathsep)
    assert marker.exists()


def test_secure_fetch_path_excludes_relative_and_checkout_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    project_bin = project / "bin"
    trusted_bin = tmp_path / "trusted-bin"
    project_bin.mkdir(parents=True)
    trusted_bin.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((".", "bin", str(project), str(project_bin), str(trusted_bin))),
    )
    repo = _repo_with_remote(project, "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.env["PATH"].split(os.pathsep) == [str(trusted_bin.resolve())]


def test_secure_fetch_path_preserves_quoted_windows_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    project_bin = project / "project bin"
    trusted_bin = tmp_path / "trusted bin"
    project_bin.mkdir(parents=True)
    trusted_bin.mkdir()
    monkeypatch.setattr(fetch_module, "is_windows", lambda: True)
    monkeypatch.setenv(
        "PATH", os.pathsep.join((f'"{project_bin}"', f'"{trusted_bin}"'))
    )
    repo = _repo_with_remote(project, "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.env["PATH"].split(os.pathsep) == [str(trusted_bin.resolve())]


def test_secure_fetch_reads_config_once_and_caches_git_exec_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.invalid/repo.git")
    original_run = subprocess.run
    calls: list[list[str]] = []

    def tracking_run(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return original_run(command, **kwargs)

    fetch_module._resolve_trusted_git_exec_path.cache_clear()
    monkeypatch.setattr(fetch_module.subprocess, "run", tracking_run)

    prepare_secure_fetch(repo, "origin")
    prepare_secure_fetch(repo, "origin")

    assert sum("--show-scope" in command for command in calls) == 2
    assert sum("--exec-path" in command for command in calls) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX command quoting")
@pytest.mark.parametrize("variable", ["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"])
def test_config_path_environment_cannot_inject_trusted_fetch_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    marker, command = _marker_command(tmp_path, variable.lower())
    injected_proxy = "http://injected.invalid:8080"
    injected = tmp_path / "injected.gitconfig"
    injected.write_text(
        f"[credential]\n\thelper = !{command}\n[http]\n\tproxy = {injected_proxy}\n"
    )
    monkeypatch.setenv(variable, str(injected))
    repo = _repo_with_remote(tmp_path / "checkout", "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")
    _run_credential_fill(repo, secure.env)
    executable = Git.GIT_PYTHON_GIT_EXECUTABLE
    assert executable is not None
    effective_http = subprocess.run(
        [executable, "-C", repo.working_dir, "config", "--get-all", "http.proxy"],
        capture_output=True,
        check=False,
        env=_process_env(secure.env),
        text=True,
    )

    assert secure.env[variable] == os.devnull
    assert not marker.exists()
    assert injected_proxy not in effective_http.stdout


@pytest.mark.parametrize(
    "variable", ["HOME", "XDG_CONFIG_HOME", "USERPROFILE", "HOMEDRIVE_HOMEPATH"]
)
def test_config_home_inside_checkout_cannot_inject_trusted_fetch_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    checkout = tmp_path / "checkout"
    repo = _repo_with_remote(checkout, "https://example.invalid/repo.git")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)

    if variable == "HOME":
        monkeypatch.setenv("HOME", str(checkout))
        config = checkout / ".gitconfig"
    elif variable == "XDG_CONFIG_HOME":
        xdg = checkout / "xdg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        config = xdg / "git" / "config"
    elif variable == "USERPROFILE":
        monkeypatch.delenv("HOME")
        monkeypatch.setattr(fetch_module, "is_windows", lambda: True)
        monkeypatch.setenv("USERPROFILE", str(checkout))
        config = checkout / ".gitconfig"
    else:
        monkeypatch.delenv("HOME")
        monkeypatch.setattr(fetch_module, "is_windows", lambda: True)
        drive, home_path = os.path.splitdrive(str(checkout))
        if not drive:
            drive = os.path.sep
            home_path = str(checkout).lstrip(os.path.sep)
        monkeypatch.setenv("HOMEDRIVE", drive)
        monkeypatch.setenv("HOMEPATH", home_path)
        config = checkout / ".gitconfig"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[credential]\n\thelper = injected\n")

    with pytest.raises(
        UnsafeGitFetchError,
        match="Global Git configuration inside the repository is not trusted",
    ):
        prepare_secure_fetch(repo, "origin")


def test_non_utf8_untrusted_global_config_value_does_not_escape_security_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_test_global_config(
        tmp_path, monkeypatch, b"[test]\n\tvalue = invalid-\xff\n"
    )
    repo = _repo_with_remote(tmp_path / "checkout", "https://example.invalid/repo.git")

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.url == "https://example.invalid/repo.git"


def test_non_utf8_trusted_global_config_value_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_test_global_config(
        tmp_path, monkeypatch, b"[credential]\n\thelper = invalid-\xff\n"
    )
    repo = _repo_with_remote(tmp_path / "checkout", "https://example.invalid/repo.git")

    with pytest.raises(
        UnsafeGitFetchError, match="Trusted Git configuration contains invalid UTF-8"
    ):
        prepare_secure_fetch(repo, "origin")


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("ssh") is None,
    reason="uses POSIX command quoting and the system SSH client",
)
@pytest.mark.parametrize("source", ["core.sshCommand", "GIT_SSH", "GIT_SSH_COMMAND"])
def test_repository_and_inherited_ssh_commands_are_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    repo = _repo_with_remote(tmp_path, "ssh://127.0.0.1:1/repo.git")
    marker, command = _marker_command(tmp_path, source.replace(".", "-").lower())
    if source == "core.sshCommand":
        repo.config_writer().set_value("core", "sshCommand", command).release()
    else:
        monkeypatch.setenv(source, command)

    with pytest.raises(GitCommandError):
        fetch_remote(repo, "origin")

    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX marker commands")
@pytest.mark.parametrize("source", ["core.gitProxy", "GIT_PROXY_COMMAND"])
def test_git_proxy_commands_are_rejected_before_git_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    repo = _repo_with_remote(tmp_path, "git://example.invalid/repo.git")
    marker, command = _marker_command(tmp_path, source.replace(".", "-").lower())
    if source == "core.gitProxy":
        repo.config_writer().set_value("core", "gitProxy", command).release()
    else:
        monkeypatch.setenv(source, command)

    with pytest.raises(UnsafeGitFetchError):
        fetch_remote(repo, "origin")

    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX marker commands")
def test_remote_helper_url_is_rejected_before_git_runs(tmp_path: Path) -> None:
    marker, command = _marker_command(tmp_path, "remote-helper-url")
    repo = _repo_with_remote(tmp_path, f"ext::{command}")

    with pytest.raises(UnsafeGitFetchError):
        fetch_remote(repo, "origin")

    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX executable")
def test_inherited_git_exec_path_cannot_replace_https_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "https-helper-ran"
    helper_dir = tmp_path / "helpers"
    helper_dir.mkdir()
    helper = helper_dir / "git-remote-https"
    helper.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 1\n")
    helper.chmod(0o755)
    monkeypatch.setenv("GIT_EXEC_PATH", str(helper_dir))
    repo = _repo_with_remote(tmp_path / "checkout", "https://127.0.0.1:1/repo.git")

    with pytest.raises(GitCommandError):
        fetch_remote(repo, "origin")

    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX marker commands")
@pytest.mark.parametrize("option", ["insteadOf", "insteadof", "InsteadOf"])
def test_repository_url_rewrite_cannot_select_a_remote_helper(
    tmp_path: Path, option: str
) -> None:
    url = "https://example.invalid/repo.git"
    repo = _repo_with_remote(tmp_path, url)
    marker, command = _marker_command(tmp_path, "remote-helper")
    config_path = Path(str(repo.git_dir)) / "config"
    with config_path.open("a") as config:
        config.write(f'\n[url "ext::{command}"]\n\t{option} = {url}\n')

    with pytest.raises(UnsafeGitFetchError):
        fetch_remote(repo, "origin")

    assert not marker.exists()


def test_repository_included_url_rewrite_is_rejected(tmp_path: Path) -> None:
    url = "https://example.invalid/repo.git"
    repo = _repo_with_remote(tmp_path / "checkout", url)
    included = tmp_path / "included.gitconfig"
    included.write_text(
        '[url "ext::malicious-helper"]\n\tinsteadOf = https://example.invalid/\n'
    )
    repo.config_writer().set_value("include", "path", str(included)).release()

    with pytest.raises(UnsafeGitFetchError, match="URL rewrites"):
        prepare_secure_fetch(repo, "origin")


def test_worktree_config_url_rewrite_is_rejected(tmp_path: Path) -> None:
    url = "https://example.invalid/repo.git"
    repo = _repo_with_remote(tmp_path, url)
    repo.config_writer().set_value("extensions", "worktreeConfig", True).release()
    (Path(str(repo.git_dir)) / "config.worktree").write_text(
        '[url "ssh://evil.example/repo.git"]\n\tinsteadOf = https://example.invalid/\n'
    )

    with pytest.raises(UnsafeGitFetchError, match="URL rewrites"):
        prepare_secure_fetch(repo, "origin")


@pytest.mark.parametrize(
    ("section", "option", "value"),
    [
        ("http", "sslVerify", "false"),
        ('http "https://example.invalid/"', "sslCAInfo", "./repo-ca.pem"),
        ("http", "proxy", "http://127.0.0.1:8080"),
        ("https", "proxy", "http://127.0.0.1:8080"),
    ],
)
def test_repository_http_config_is_rejected(
    tmp_path: Path, section: str, option: str, value: str
) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.invalid/repo.git")
    repo.config_writer().set_value(section, option, value).release()

    with pytest.raises(UnsafeGitFetchError, match="HTTP configuration"):
        prepare_secure_fetch(repo, "origin")


def test_repository_included_http_config_is_rejected(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path / "checkout", "https://example.invalid/repo.git")
    included = tmp_path / "included.gitconfig"
    included.write_text("[http]\n\tsslVerify = false\n")
    repo.config_writer().set_value("include", "path", str(included)).release()

    with pytest.raises(UnsafeGitFetchError, match="HTTP configuration"):
        prepare_secure_fetch(repo, "origin")


def test_worktree_http_config_is_rejected(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.invalid/repo.git")
    repo.config_writer().set_value("extensions", "worktreeConfig", True).release()
    (Path(str(repo.git_dir)) / "config.worktree").write_text(
        "[http]\n\tsslVerify = false\n"
    )

    with pytest.raises(UnsafeGitFetchError, match="HTTP configuration"):
        prepare_secure_fetch(repo, "origin")


def test_linked_worktree_without_worktree_config_can_fetch(tmp_path: Path) -> None:
    url = "https://example.invalid/repo.git"
    repo = _repo_with_remote(tmp_path / "checkout", url)
    repo.index.commit("initial")
    linked_path = tmp_path / "linked"
    repo.git.worktree("add", "-b", "linked", str(linked_path))
    linked = Repo(linked_path)

    secure = prepare_secure_fetch(linked, "origin")

    assert secure.url == url


def test_local_file_fetch_ignores_irrelevant_repository_http_config(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    repo = _repo_with_remote(tmp_path / "checkout", str(remote))
    repo.config_writer().set_value("http", "sslVerify", "false").release()

    secure = prepare_secure_fetch(repo, "origin", allow_file=True)

    assert secure.url == str(remote)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/owner/repo.git",
        "ssh://git@example.com/owner/repo.git",
        "git@example.com:owner/repo.git",
    ],
)
def test_secure_fetch_preserves_https_and_ssh_urls(tmp_path: Path, url: str) -> None:
    if url.startswith(("ssh://", "git@")) and shutil.which("ssh") is None:
        pytest.skip("system SSH client is unavailable")
    repo = _repo_with_remote(tmp_path, url)

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.url == url
    assert secure.env["GIT_ALLOW_PROTOCOL"] == "https:ssh"
    assert secure.env["GIT_EXEC_PATH"]


def test_windows_ssh_command_uses_windows_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "is_windows", lambda: True)

    command = fetch_module._quote_ssh_command(r"C:\Program Files\OpenSSH\ssh.exe")

    assert command == r'"C:\Program Files\OpenSSH\ssh.exe"'


@pytest.mark.parametrize(
    "url",
    [
        "-oProxyCommand=marker:repo.git",
        "git@-oProxyCommand=marker:repo.git",
        "ssh://-oProxyCommand=marker/repo.git",
        "ssh://example.com:not-a-port/repo.git",
    ],
)
def test_ssh_option_injection_urls_are_rejected(tmp_path: Path, url: str) -> None:
    repo = _repo_with_remote(tmp_path, url)

    with pytest.raises(UnsafeGitFetchError):
        prepare_secure_fetch(repo, "origin")


def test_local_file_transport_requires_explicit_opt_in(tmp_path: Path) -> None:
    remote_path = tmp_path / "remote.git"
    remote_path.mkdir()
    repo = _repo_with_remote(tmp_path / "checkout", str(remote_path))

    with pytest.raises(UnsafeGitFetchError):
        prepare_secure_fetch(repo, "origin")

    secure = prepare_secure_fetch(repo, "origin", allow_file=True)
    assert secure.url == str(remote_path)
    assert secure.env["GIT_ALLOW_PROTOCOL"] == "https:ssh:file"


@pytest.mark.parametrize(
    "url",
    [
        "/srv/git/upstream.git",
        "repos/upstream.git",
        "./upstream.git",
        "../upstream.git",
        "file:///srv/git/upstream.git",
        "file:/srv/git/upstream.git",
        r"\repos\upstream.git",
    ],
)
def test_file_opt_in_accepts_only_local_path_spellings(url: str) -> None:
    assert fetch_module._validate_fetch_url(url, allow_file=True) == "file"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("file:///srv/git/upstream.git", "/srv/git/upstream.git"),
        ("file:/srv/git/upstream.git", "/srv/git/upstream.git"),
        ("file:///srv/git/upstream%20repo.git", "/srv/git/upstream repo.git"),
        ("file:///srv/git/nested%2Frepo.git", "/srv/git/nested/repo.git"),
    ],
)
def test_file_url_is_canonicalized_before_git_runs(
    tmp_path: Path, url: str, expected: str
) -> None:
    repo = _repo_with_remote(tmp_path, url)

    secure = prepare_secure_fetch(repo, "origin", allow_file=True)

    assert secure.url == expected


@pytest.mark.parametrize("url", [r"C:\repos\upstream.git", "C:/repos/upstream.git"])
def test_file_opt_in_accepts_windows_drive_paths_only_on_windows(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_module, "is_windows", lambda: True)
    assert fetch_module._validate_fetch_url(url, allow_file=True) == "file"

    monkeypatch.setattr(fetch_module, "is_windows", lambda: False)
    with pytest.raises(UnsafeGitFetchError):
        fetch_module._validate_fetch_url(url, allow_file=True)


def test_windows_file_url_is_canonicalized_to_drive_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_module, "is_windows", lambda: True)
    repo = _repo_with_remote(tmp_path, "file:///C:/repos/upstream.git")

    secure = prepare_secure_fetch(repo, "origin", allow_file=True)

    assert secure.url == "C:/repos/upstream.git"


@pytest.mark.parametrize(
    "url",
    [
        "//server/share/upstream.git",
        r"\\server\share\upstream.git",
        r"/\server\share\upstream.git",
        r"\/server/share/upstream.git",
        r"\\?\UNC\server\share\upstream.git",
        "file://server/share/upstream.git",
        "file://localhost/share/upstream.git",
        "file:////server/share/upstream.git",
        "file://C:/repos/upstream.git",
        "file:///%2Fserver/share/upstream.git",
        "file:///%2fserver/share/upstream.git",
        "file:///%5C%5Cserver/share/upstream.git",
        r"file:/\\server\share\upstream.git",
        "file:///srv/git/nested%5Crepo.git",
        "file:///srv/git/repo.git?transport=ssh",
        "file:///srv/git/repo.git#transport=ssh",
        "file:///srv/git/repo%ZZ.git",
        "--upload-pack=helper",
        "-c",
    ],
)
def test_file_opt_in_rejects_network_and_option_like_paths(url: str) -> None:
    with pytest.raises(UnsafeGitFetchError):
        fetch_module._validate_fetch_url(url, allow_file=True)


def test_project_and_relative_path_ssh_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    trusted_bin = tmp_path / "trusted-bin"
    repo = _repo_with_remote(project, "ssh://git@example.com/owner/repo.git")
    executable_name = "ssh.exe" if sys.platform == "win32" else "ssh"
    planted = _write_executable(project / executable_name)
    trusted = _write_executable(trusted_bin / executable_name)
    original_path = os.environ.get("PATH", "")
    monkeypatch.chdir(project)
    monkeypatch.setenv(
        "PATH", os.pathsep.join((".", str(project), str(trusted_bin), original_path))
    )

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.env["GIT_SSH_COMMAND"] == fetch_module._quote_ssh_command(
        str(trusted.resolve())
    )
    assert str(planted.resolve()) not in secure.env["GIT_SSH_COMMAND"]


def test_windows_cwd_planted_ssh_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    trusted_bin = tmp_path / "trusted-bin"
    repo = _repo_with_remote(project, "ssh://git@example.com/owner/repo.git")
    planted = _write_executable(project / "ssh.exe")
    _write_executable(project / "ssh.bat")
    trusted = _write_executable(trusted_bin / "ssh.exe")
    original_path = os.environ.get("PATH", "")
    monkeypatch.setattr("vibe.utils.platform.is_windows", lambda: True)
    monkeypatch.setattr(fetch_module, "is_windows", lambda: True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATHEXT", os.pathsep.join((".EXE", ".BAT", ".CMD")))
    monkeypatch.setenv("PATH", os.pathsep.join((str(trusted_bin), original_path)))

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.env["GIT_SSH_COMMAND"] == subprocess.list2cmdline([
        str(trusted.resolve())
    ])
    assert str(planted.resolve()) not in secure.env["GIT_SSH_COMMAND"]
    assert "ssh.bat" not in secure.env["GIT_SSH_COMMAND"]


def test_project_only_ssh_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    repo = _repo_with_remote(project, "ssh://git@example.com/owner/repo.git")
    executable_name = "ssh.exe" if sys.platform == "win32" else "ssh"
    _write_executable(project / executable_name)
    monkeypatch.setenv("PATH", os.pathsep.join((".", str(project))))

    with pytest.raises(UnsafeGitFetchError, match="ssh not found"):
        prepare_secure_fetch(repo, "origin")


def test_https_without_ssh_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.com/owner/repo.git")
    monkeypatch.setattr(fetch_module, "resolve_ssh_executable", lambda **_kwargs: None)

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.env["GIT_SSH_COMMAND"] == ""


def test_https_fetch_prepares_ssh_for_trusted_global_url_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.invalid/owner/repo.git"
    project = tmp_path / "checkout"
    executable_name = "ssh.exe" if sys.platform == "win32" else "ssh"
    trusted = _write_executable(tmp_path / "trusted-bin" / executable_name)
    repo = _repo_with_remote(project, url)
    _write_test_global_config(
        tmp_path,
        monkeypatch,
        '[url "ssh://git@example.invalid/"]\n\tinsteadOf = https://example.invalid/\n',
    )
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(trusted.parent), os.environ.get("PATH", "")))
    )

    secure = prepare_secure_fetch(repo, "origin")

    assert secure.env["GIT_SSH_COMMAND"] == fetch_module._quote_ssh_command(
        str(trusted.resolve())
    )


def test_secure_fetch_overrides_repository_hooks_path(tmp_path: Path) -> None:
    repo = _repo_with_remote(tmp_path, "https://example.com/owner/repo.git")
    repo.config_writer().set_value(
        "core", "hooksPath", str(tmp_path / "evil-hooks")
    ).release()

    secure = prepare_secure_fetch(repo, "origin")

    config = _config_from_env(secure.env)
    assert config["core.fsmonitor"] == ""
    assert config["core.hooksPath"] == fetch_module._NO_HOOKS_PATH


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX hook script")
@pytest.mark.parametrize("use_configured_hooks_path", [False, True])
def test_repository_hooks_are_not_executed(
    tmp_path: Path, use_configured_hooks_path: bool
) -> None:
    source_dir = tmp_path / "source"
    remote_dir = tmp_path / "remote.git"
    checkout_dir = tmp_path / "checkout"
    source = Repo.init(source_dir)
    source.config_writer().set_value("user", "name", "Tester").release()
    source.config_writer().set_value("user", "email", "t@example.com").release()
    (source_dir / "README").write_text("hi\n")
    source.index.add(["README"])
    source.index.commit("init")
    Repo.init(remote_dir, bare=True)
    source.create_remote("origin", str(remote_dir)).push("HEAD:refs/heads/main")

    repo = Repo.init(checkout_dir)
    repo.create_remote("origin", str(remote_dir))
    marker = tmp_path / "hook-ran"
    hooks_dir = (
        tmp_path / "evil-hooks"
        if use_configured_hooks_path
        else Path(str(repo.git_dir)) / "hooks"
    )
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "reference-transaction"
    hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    hook.chmod(0o755)
    if use_configured_hooks_path:
        repo.config_writer().set_value("core", "hooksPath", str(hooks_dir)).release()

    fetch_remote(
        repo, "origin", ("+refs/heads/*:refs/remotes/origin/*",), allow_file=True
    )

    assert repo.git.rev_parse("refs/remotes/origin/main")
    assert not marker.exists()
