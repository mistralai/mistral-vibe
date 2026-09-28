from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import TYPE_CHECKING, Any
from urllib.parse import SplitResult, unquote, urlsplit

from vibe.core.utils import is_windows
from vibe.utils.platform import resolve_ssh_executable

if TYPE_CHECKING:
    from git import Repo


_SCP_SSH_URL = re.compile(
    r"^(?:(?P<user>[^/@:\s]+)@)?(?P<host>[^/:\s]+):(?P<path>(?!:).+)$"
)
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_NETWORK_PATH_PREFIX = re.compile(r"^[\\/]{2}")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_URL_REWRITE_KEY = re.compile(r"^url\..+\.insteadof$", re.IGNORECASE)
_NO_HOOKS_PATH = "/nonexistent-vibe-disabled-git-hooks"
_TRUSTED_FETCH_SCOPES = frozenset({"system", "global"})


class UnsafeGitFetchError(ValueError):
    """The configured fetch would cross the trusted Git execution boundary."""


@dataclass(frozen=True)
class SecureFetch:
    url: str
    env: dict[str, str]


def prepare_secure_fetch(
    repo: Repo, remote: str, *, allow_file: bool = False
) -> SecureFetch:
    """Resolve a remote without letting repository configuration execute code.

    Git treats several configuration values as commands. Repository config is
    attacker-controlled whenever Vibe opens an untrusted checkout, so fetches
    use an explicit, allowlisted URL and command-scope overrides for every
    executable setting used by the supported SSH and HTTPS transports.
    """
    configured_url = _remote_url(repo, remote)
    protocol, url = _validated_fetch_url(configured_url, allow_file=allow_file)
    trusted_fetch_config = _inspect_fetch_config(
        repo, url=url, reject_repository_http=protocol != "file"
    )

    config = [
        ("credential.helper", ""),
        *trusted_fetch_config,
        ("core.askPass", ""),
        ("core.fsmonitor", ""),
        ("core.gitProxy", ""),
        ("core.hooksPath", _NO_HOOKS_PATH),
        ("core.sshCommand", ""),
        ("fetch.recurseSubmodules", "false"),
        ("submodule.recurse", "false"),
        ("protocol.allow", "never"),
        ("protocol.https.allow", "always"),
        ("protocol.ssh.allow", "always"),
    ]
    if allow_file:
        config.append(("protocol.file.allow", "always"))
    env = _config_environment(config)
    env.update({
        "GCM_INTERACTIVE": "never",
        "GIT_ALLOW_PROTOCOL": "https:ssh:file" if allow_file else "https:ssh",
        "GIT_ASKPASS": "",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_PARAMETERS": "",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_EXEC_PATH": _trusted_git_exec_path(repo),
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_PROXY_COMMAND": "",
        "GIT_SSH": "",
        "GIT_SSH_COMMAND": "",
        "GIT_SSH_VARIANT": "ssh",
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": _trusted_path(repo),
        "SSH_ASKPASS": "",
        "SSH_ASKPASS_REQUIRE": "never",
    })
    ssh = _trusted_ssh_executable(repo)
    if ssh is not None:
        # Trusted global insteadOf rules may rewrite an HTTPS URL to SSH after
        # validation, so prepare the safe client for both supported protocols.
        env["GIT_SSH_COMMAND"] = _quote_ssh_command(ssh)
    elif protocol == "ssh":
        raise UnsafeGitFetchError("Cannot securely fetch SSH remote: ssh not found")

    return SecureFetch(url=url, env=env)


def fetch_remote(
    repo: Repo,
    remote: str,
    refspecs: Sequence[str] = (),
    *,
    allow_file: bool = False,
    **kwargs: Any,
) -> Any:
    """Fetch an SSH/HTTPS remote under the shared untrusted-repository policy."""
    secure = prepare_secure_fetch(repo, remote, allow_file=allow_file)
    return repo.git.fetch(
        "--no-recurse-submodules",
        "--no-auto-maintenance",
        secure.url,
        *refspecs,
        env=secure.env,
        **kwargs,
    )


def _remote_url(repo: Repo, remote: str) -> str:
    try:
        value = repo.remote(remote).config_reader.get("url")
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise UnsafeGitFetchError(f"Remote {remote!r} has no fetch URL") from e
    if not isinstance(value, str) or not value:
        raise UnsafeGitFetchError(f"Remote {remote!r} has no fetch URL")
    return value


def _validate_fetch_url(url: str, *, allow_file: bool) -> str:
    protocol, _ = _validated_fetch_url(url, allow_file=allow_file)
    return protocol


def _validated_fetch_url(url: str, *, allow_file: bool) -> tuple[str, str]:
    if any(character in url for character in ("\0", "\r", "\n")):
        raise UnsafeGitFetchError("Remote URL contains control characters")
    if allow_file and (local_path := _canonical_local_file_url(url)) is not None:
        return "file", local_path
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname:
        return parsed.scheme, url
    if parsed.scheme == "ssh" and _valid_ssh_endpoint(parsed.username, parsed.hostname):
        try:
            _ = parsed.port
        except ValueError as e:
            raise UnsafeGitFetchError("SSH remote URL has an invalid port") from e
        return parsed.scheme, url
    if not parsed.scheme and (match := _SCP_SSH_URL.fullmatch(url)):
        if _valid_ssh_endpoint(match.group("user"), match.group("host")):
            return "ssh", url
    raise UnsafeGitFetchError("Only explicit SSH and HTTPS remote URLs may be fetched")


def _canonical_local_file_url(url: str) -> str | None:
    """Return a Git-local path, or ``None`` for non-local and ambiguous URLs."""
    # Windows accepts either slash at both leading UNC positions. Reject every
    # combination on all platforms because the checkout can later be opened on
    # Windows and trigger SMB authentication there.
    if _NETWORK_PATH_PREFIX.match(url):
        return None
    if is_windows() and _WINDOWS_DRIVE_PATH.match(url):
        return url

    parsed = urlsplit(url)
    if parsed.scheme == "file":
        return _canonical_file_scheme_path(parsed)
    if parsed.scheme:
        return None
    if _SCP_SSH_URL.fullmatch(url):
        return None
    # A leading dash can be reinterpreted as an option by callers or future Git
    # plumbing. Every other scheme-less spelling is a Git-local path, including
    # ordinary relatives such as ``repos/upstream.git``.
    return url if not url.startswith("-") else None


def _canonical_file_scheme_path(parsed: SplitResult) -> str | None:
    """Decode a file URL into the unambiguous local path Git will receive."""
    # Git percent-decodes file URLs and treats backslashes as transport syntax
    # on some platforms. Validate the decoded representation and pass that path
    # onward instead of passing the differently parsed original URL to Git.
    raw_path = parsed.path
    has_url_suffix = any((parsed.netloc, parsed.query, parsed.fragment))
    has_ambiguous_path = (
        not raw_path.startswith("/") or raw_path.startswith("//") or "\\" in raw_path
    )
    has_ambiguous_encoding = bool(_INVALID_PERCENT_ESCAPE.search(raw_path))
    if has_url_suffix or has_ambiguous_path or has_ambiguous_encoding:
        return None
    try:
        local_path = unquote(raw_path, errors="strict")
    except UnicodeDecodeError:
        return None
    if (
        any(character in local_path for character in ("\0", "\r", "\n"))
        or "\\" in local_path
        or local_path.startswith("//")
    ):
        return None
    if is_windows() and re.match(r"^/[A-Za-z]:/", local_path):
        return local_path[1:]
    return local_path


def _valid_ssh_endpoint(user: str | None, host: str | None) -> bool:
    return bool(
        host and not host.startswith("-") and (user is None or not user.startswith("-"))
    )


def _trusted_ssh_executable(repo: Repo) -> str | None:
    working_dir = getattr(repo, "working_dir", None)
    git_dir = getattr(repo, "git_dir", None)
    project_dir = Path(str(working_dir or git_dir or Path.cwd())).resolve()
    return resolve_ssh_executable(cwd=project_dir)


def _trusted_path(repo: Repo) -> str:
    """Drop relative and checkout-controlled entries from inherited PATH."""
    working_dir = getattr(repo, "working_dir", None)
    git_dir = getattr(repo, "git_dir", None)
    project_dir = Path(str(working_dir or git_dir or Path.cwd())).resolve()
    trusted: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if is_windows() and entry.startswith('"') and entry.endswith('"'):
            entry = entry.removeprefix('"').removesuffix('"')
        candidate = Path(entry).expanduser()
        if not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == project_dir or project_dir in resolved.parents:
            continue
        trusted.append(str(resolved))
    return os.pathsep.join(trusted)


def _quote_ssh_command(executable: str) -> str:
    if is_windows():
        return subprocess.list2cmdline([executable])
    return shlex.quote(executable)


def _is_trusted_fetch_config_key(key: str) -> bool:
    """Return whether a protected-scope key may be replayed for a fetch."""
    normalized_key = key.casefold()
    return (
        normalized_key.startswith(("credential.", "http.", "https."))
        or normalized_key == "safe.directory"
        or _URL_REWRITE_KEY.fullmatch(key) is not None
    )


def _inspect_fetch_config(
    repo: Repo, *, url: str, reject_repository_http: bool
) -> list[tuple[str, str]]:
    """Read all Git scopes once and retain only trusted fetch configuration.

    The complete policy requires Git 2.36 or newer. ``--show-scope`` itself was
    added in Git 2.26, while command-scope ``safe.directory`` support requires
    Git 2.36. An unavailable scoped read fails closed rather than treating an
    unclassified configuration as trusted.
    """
    executable = _trusted_git_executable(repo)
    env = _git_config_read_environment(repo)
    git_dir = Path(str(repo.git_dir)).resolve()
    working_dir = getattr(repo, "working_dir", None)
    command = [executable, f"--git-dir={git_dir}"]
    if working_dir is not None:
        command.append(f"--work-tree={Path(str(working_dir)).resolve()}")

    try:
        result = subprocess.run(
            [*command, "config", "--includes", "--show-scope", "--null", "--list"],
            capture_output=True,
            encoding="utf-8",
            env=env,
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise UnsafeGitFetchError("Cannot inspect Git configuration for fetch") from e
    if result.returncode != 0:
        raise UnsafeGitFetchError("Cannot inspect Git configuration for fetch")

    records = result.stdout.split("\0")
    if records and not records[-1]:
        records.pop()
    if len(records) % 2:
        raise UnsafeGitFetchError("Cannot inspect Git configuration for fetch")

    trusted_config: list[tuple[str, str]] = []
    for index in range(0, len(records), 2):
        scope = records[index].casefold()
        key, separator, value = records[index + 1].partition("\n")
        if not separator:
            raise UnsafeGitFetchError("Cannot inspect Git configuration for fetch")
        normalized_key = key.casefold()
        if (
            reject_repository_http
            and normalized_key.startswith(("http.", "https."))
            and scope not in _TRUSTED_FETCH_SCOPES
        ):
            raise UnsafeGitFetchError(
                "Repository HTTP configuration is not allowed for fetches"
            )
        if (
            scope not in _TRUSTED_FETCH_SCOPES
            and _URL_REWRITE_KEY.fullmatch(key)
            and url.startswith(value)
        ):
            raise UnsafeGitFetchError(
                "Repository URL rewrites are not allowed for fetches"
            )
        if scope in _TRUSTED_FETCH_SCOPES and _is_trusted_fetch_config_key(key):
            if "\ufffd" in key or "\ufffd" in value:
                raise UnsafeGitFetchError(
                    "Trusted Git configuration contains invalid UTF-8"
                )
            trusted_config.append((key, value))
    return trusted_config


def _git_config_read_environment(repo: Repo) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GIT_CONFIG_GLOBAL", None)
    env.pop("GIT_CONFIG_SYSTEM", None)
    env.pop("GIT_CONFIG_PARAMETERS", None)
    env.pop("GIT_CONFIG_COUNT", None)
    for key in tuple(env):
        if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            del env[key]
    _ensure_global_config_paths_are_trusted(repo, env)
    return env


def _ensure_global_config_paths_are_trusted(repo: Repo, env: dict[str, str]) -> None:
    """Reject user config files that resolve inside the untrusted checkout."""
    working_dir = getattr(repo, "working_dir", None)
    git_dir = getattr(repo, "git_dir", None)
    project_dir = Path(str(working_dir or git_dir or Path.cwd())).resolve()

    home_value = env.get("HOME")
    home_directories = [Path(home_value)] if home_value else []
    if not home_directories and is_windows():
        if user_profile := env.get("USERPROFILE"):
            home_directories.append(Path(user_profile))
        home_drive = env.get("HOMEDRIVE")
        home_path = env.get("HOMEPATH")
        if home_drive and home_path:
            home_directories.append(Path(f"{home_drive}{home_path}"))

    config_paths = [home / ".gitconfig" for home in home_directories]

    xdg_value = env.get("XDG_CONFIG_HOME")
    if xdg_value:
        config_paths.append(Path(xdg_value) / "git" / "config")
    else:
        config_paths.extend(
            home / ".config" / "git" / "config" for home in home_directories
        )

    for config_path in config_paths:
        try:
            resolved = config_path.resolve()
        except OSError as e:
            raise UnsafeGitFetchError(
                "Cannot resolve global Git configuration path"
            ) from e
        if resolved == project_dir or project_dir in resolved.parents:
            raise UnsafeGitFetchError(
                "Global Git configuration inside the repository is not trusted"
            )


def _config_environment(config: Sequence[tuple[str, str]]) -> dict[str, str]:
    env = {"GIT_CONFIG_COUNT": str(len(config))}
    for index, (key, value) in enumerate(config):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _trusted_git_exec_path(repo: Repo) -> str:
    executable = _trusted_git_executable(repo)
    return _resolve_trusted_git_exec_path(executable)


@cache
def _resolve_trusted_git_exec_path(executable: str) -> str:
    env = os.environ.copy()
    env.pop("GIT_EXEC_PATH", None)
    try:
        result = subprocess.run(
            [executable, "--exec-path"],
            check=True,
            capture_output=True,
            env=env,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise UnsafeGitFetchError("Cannot resolve Git's trusted helper path") from e
    path = result.stdout.strip()
    if not path:
        raise UnsafeGitFetchError("Cannot resolve Git's trusted helper path")
    return path


def _trusted_git_executable(repo: Repo) -> str:
    executable = repo.git.GIT_PYTHON_GIT_EXECUTABLE
    if not executable:
        raise UnsafeGitFetchError("Cannot resolve Git's trusted helper path")
    return executable
