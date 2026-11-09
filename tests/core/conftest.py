from __future__ import annotations

from pathlib import Path

from git import Git, Repo
from git.exc import GitCommandError
from git.refs.symbolic import SymbolicReference
import pytest


@pytest.fixture
def git_cli_repo(tmp_path: Path, request: pytest.FixtureRequest) -> Repo:
    """An empty repository at tmp_path, in the ref format given as the param.

    Write to it with the git CLI only: GitPython's own ref writes do not
    support reftable. Reftable needs git 2.45 or later, so it skips on older
    git, CI included; `no_gitpython_ref_files` covers the same reads there.
    """
    ref_format: str = getattr(request, "param", "files")
    # Older git rejects --ref-format even for files, its only format.
    options = () if ref_format == "files" else (f"--ref-format={ref_format}",)
    try:
        Git(tmp_path).init("--initial-branch=main", *options)
    except GitCommandError:
        pytest.skip(f"this git cannot create {ref_format} repositories")
    repo = Repo(tmp_path)
    repo.git.config("user.name", "Tester")
    repo.git.config("user.email", "t@example.com")
    return repo


@pytest.fixture
def no_gitpython_ref_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make GitPython's reads of ref files fail, on any git version.

    GitPython reads HEAD (`repo.head`, `active_branch`) and lists branches
    (`repo.heads`) from .git/HEAD, the loose ref files and packed-refs. A
    reftable repository keeps none of them, so code that must work there has
    to ask git. The test can still set the repository up with the git CLI.
    """

    def unreadable(*_args: object, **_kwargs: object) -> None:
        raise ValueError("ref files are placeholders")

    monkeypatch.setattr(SymbolicReference, "_get_ref_info", unreadable)
    monkeypatch.setattr(SymbolicReference, "_iter_items", unreadable)
