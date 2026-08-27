from __future__ import annotations

from configparser import NoOptionError, NoSectionError
from dataclasses import dataclass
from typing import TYPE_CHECKING

# giturlparse is a URL parser and resolves nothing on the machine, unlike
# GitPython, so it is safe to import here. Anything needing a Repo takes one the
# caller has already opened.
from giturlparse import parse as parse_git_url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from git import Remote, Repo


@dataclass(frozen=True)
class GitHubRemoteInfo:
    name: str
    owner: str
    repo: str


def find_remote_url(repo: Repo) -> str | None:
    """The repository this checkout points at, normalised to an https URL.

    Host-agnostic: GitHub, GitLab including its subgroups, Bitbucket and a
    self-hosted server all answer the same way. Only teleport cares which host
    it is, because only teleport has to clone it from the other side, and that
    is a teleport policy rather than a fact about the checkout.

    Normalised rather than reported as configured, because ssh and https spell
    the same repository differently and a caller matching this against a
    project's repositories needs one of them.

    None when the repository has no remote, and when none of them parses as a
    git URL at all.
    """
    for remote in repo.remotes:
        for url in _remote_urls(remote):
            if normalised := normalise_remote_url(url):
                return normalised
    return None


def normalise_remote_url(url: str) -> str | None:
    """One spelling of the repository a remote URL names, or None."""
    parsed = parse_git_url(url)
    if not (parsed.valid and parsed.host and parsed.owner and parsed.repo):
        return None
    # `owner` and `repo` skip whatever sits between them, which on GitLab is a
    # subgroup and part of the repository's identity.
    path = "/".join([parsed.owner, *parsed.groups, parsed.repo])
    return f"https://{parsed.host}/{path}.git"


def find_github_remote(repo: Repo) -> GitHubRemoteInfo | None:
    """The first remote pointing at GitHub specifically, or None.

    Narrower than `find_remote_url` on purpose: teleport builds a cloud session
    from this and the cloud can only clone GitHub, so a GitLab remote has to
    read as no remote there even though it is a perfectly good one here.
    """
    for remote in repo.remotes:
        for url in _remote_urls(remote):
            if parsed := parse_github_url(url):
                owner, repo_name = parsed
                return GitHubRemoteInfo(name=remote.name, owner=owner, repo=repo_name)
    return None


def to_https_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def _remote_urls(remote: Remote) -> Iterator[str]:
    """The configured URL first, then whatever else the remote reports.

    A generator because reading ``remote.urls`` shells out to
    ``git config --get-all`` while the configured URL is already in hand, and
    the configured URL answers for all but multi-URL remotes. The caller stops
    at the first match, so that subprocess is usually never spawned.
    """
    seen: set[str] = set()
    config_reader = getattr(remote, "config_reader", None)
    try:
        raw_url = config_reader.get("url") if config_reader is not None else None
    except (AttributeError, NoOptionError, NoSectionError, TypeError, ValueError):
        raw_url = None
    if isinstance(raw_url, str):
        seen.add(raw_url)
        yield raw_url

    for url in getattr(remote, "urls", ()):
        if isinstance(url, str) and url not in seen:
            seen.add(url)
            yield url


def parse_github_url(url: str) -> tuple[str, str] | None:
    """The owner and repository a GitHub remote URL names, in any of its forms.

    None for a URL pointing somewhere other than GitHub, and for anything that
    does not parse as a git URL at all.
    """
    parsed = parse_git_url(url)
    if parsed.github and parsed.owner and parsed.repo:
        return parsed.owner, parsed.repo
    return None
