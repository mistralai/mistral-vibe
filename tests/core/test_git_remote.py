from __future__ import annotations

from configparser import NoOptionError
from typing import TYPE_CHECKING, Any, cast

from vibe.core.git.remote import (
    find_github_remote,
    find_remote_url,
    normalise_remote_url,
    parse_github_url,
    to_https_url,
)

if TYPE_CHECKING:
    from git import Repo


class _ConfigReader:
    def __init__(self, url: str | None) -> None:
        self._url = url

    def get(self, key: str) -> str:
        if key != "url" or self._url is None:
            raise NoOptionError(key, "remote")
        return self._url


class _Remote:
    """Enough of a GitPython remote to drive the lookup.

    `urls` records that it was read, because reading the real one shells out to
    `git config --get-all` and the point of the generator is to not do that.
    """

    def __init__(self, name: str, *, config_url: str | None, urls: list[str]) -> None:
        self.name = name
        self.config_reader = _ConfigReader(config_url)
        self._urls = urls
        self.urls_read = False

    @property
    def urls(self) -> list[str]:
        self.urls_read = True
        return self._urls


class _Repo:
    def __init__(self, *remotes: _Remote) -> None:
        self.remotes = list(remotes)


def _repo(*remotes: _Remote) -> Repo:
    """The fake, cast at the boundary: the lookups only ever read `remotes`."""
    return cast("Repo", _Repo(*remotes))


def _find(*remotes: _Remote) -> Any:
    return find_github_remote(_repo(*remotes))


def test_finds_a_github_remote_from_its_configured_url() -> None:
    found = _find(
        _Remote("origin", config_url="git@github.com:mistralai/dashboard.git", urls=[])
    )

    assert found is not None
    assert (found.name, found.owner, found.repo) == ("origin", "mistralai", "dashboard")


def test_does_not_read_the_remote_urls_when_the_configured_one_matched() -> None:
    remote = _Remote(
        "origin",
        config_url="https://github.com/mistralai/dashboard",
        urls=["https://github.com/other/other.git"],
    )

    assert _find(remote) is not None
    assert remote.urls_read is False


def test_falls_back_to_the_remote_urls_when_the_configured_one_does_not_match() -> None:
    remote = _Remote(
        "origin",
        config_url="git@gitlab.com:owner/repo.git",
        urls=["git@github.com:mistralai/dashboard.git"],
    )

    found = _find(remote)

    assert found is not None
    assert found.owner == "mistralai"
    assert remote.urls_read is True


def test_reads_the_remote_urls_when_there_is_no_configured_url() -> None:
    found = _find(
        _Remote(
            "origin", config_url=None, urls=["git@github.com:mistralai/dashboard.git"]
        )
    )

    assert found is not None
    assert found.repo == "dashboard"


def test_takes_the_first_github_remote_in_order() -> None:
    found = _find(
        _Remote("upstream", config_url="git@gitlab.com:owner/repo.git", urls=[]),
        _Remote("origin", config_url="git@github.com:mistralai/dashboard.git", urls=[]),
    )

    assert found is not None
    assert found.name == "origin"


def test_returns_none_when_no_remote_is_on_github() -> None:
    assert (
        _find(_Remote("origin", config_url="git@gitlab.com:o/r.git", urls=[])) is None
    )


def test_returns_none_without_any_remote() -> None:
    assert _find() is None


def test_normalises_every_spelling_of_one_repository() -> None:
    spellings = [
        "git@github.com:mistralai/dashboard.git",
        "git@github.com:mistralai/dashboard",
        "https://github.com/mistralai/dashboard.git",
        "https://github.com/mistralai/dashboard",
    ]

    normalised = {
        to_https_url(*parsed)
        for url in spellings
        if (parsed := parse_github_url(url)) is not None
    }

    assert normalised == {"https://github.com/mistralai/dashboard.git"}


def test_parses_nothing_out_of_a_url_that_is_not_one() -> None:
    assert parse_github_url("not-a-valid-url") is None


# Where the repository is hosted is not this module's business. Only teleport
# narrows to GitHub, because only teleport has to clone it from the other side.
def test_normalises_a_remote_on_any_host() -> None:
    assert normalise_remote_url("git@gitlab.com:owner/repo.git") == (
        "https://gitlab.com/owner/repo.git"
    )
    assert normalise_remote_url("git@bitbucket.org:owner/repo.git") == (
        "https://bitbucket.org/owner/repo.git"
    )
    assert normalise_remote_url("ssh://git@git.internal.corp:2222/team/repo.git") == (
        "https://git.internal.corp/team/repo.git"
    )


# `owner` and `repo` skip whatever sits between them, and on GitLab that is a
# subgroup the repository cannot be identified without.
def test_keeps_the_subgroups_of_a_gitlab_remote() -> None:
    assert normalise_remote_url("https://gitlab.com/group/sub/repo.git") == (
        "https://gitlab.com/group/sub/repo.git"
    )


def test_normalises_nothing_out_of_a_url_that_is_not_one() -> None:
    assert normalise_remote_url("not-a-valid-url") is None


def test_finds_a_remote_on_any_host() -> None:
    found = find_remote_url(
        _repo(_Remote("origin", config_url="git@gitlab.com:owner/repo.git", urls=[]))
    )

    assert found == "https://gitlab.com/owner/repo.git"


def test_finds_no_remote_url_without_any_remote() -> None:
    assert find_remote_url(_repo()) is None
