from __future__ import annotations


class GitError(Exception): ...


class GitUnavailableError(GitError): ...


class GitRepositoryNotFoundError(GitError): ...
