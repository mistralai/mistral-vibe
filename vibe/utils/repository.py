from __future__ import annotations

import re
from urllib.parse import urlparse

_SSH_REPO_URL_RE = re.compile(r"^(?:ssh://)?git@(?P<host>[^/:]+)[:/](?P<path>.+)$")


def normalize_repo_url(repo_url: str) -> str:
    value = repo_url.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = f"github.com/{value.removeprefix('git@github.com:')}"
    else:
        parsed = urlparse(value)
        if parsed.netloc and parsed.path:
            value = f"{parsed.netloc}/{parsed.path.lstrip('/')}"
    value = value.rstrip("/")
    return value[:-4].lower() if value.endswith(".git") else value.lower()


def repo_url_label(repo_url: str) -> str:
    value = repo_url.strip().rstrip("/")
    if ssh_match := _SSH_REPO_URL_RE.match(value):
        value = f"{ssh_match.group('host')}/{ssh_match.group('path')}"
    else:
        parsed = urlparse(value)
        if parsed.netloc and parsed.path:
            value = f"{parsed.netloc}/{parsed.path.lstrip('/')}"
    value = value.rstrip("/")
    return value[:-4] if value.endswith(".git") else value


__all__ = ["normalize_repo_url", "repo_url_label"]
