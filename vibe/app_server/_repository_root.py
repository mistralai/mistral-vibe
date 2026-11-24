"""The git repository a session's folder belongs to, for grouping session rows.

A linked worktree groups under its main checkout, so every checkout of one
repository shares a root. Only the filesystem is read, never git itself: a
listing asks this for every folder it shows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import itertools
from pathlib import Path

from vibe.core.git.worktree import ManagedWorktree

_GITDIR_PREFIX = "gitdir:"


@dataclass(frozen=True)
class Checkout:
    """Where a folder sits in git: the checkout holding it and its repository.

    ``checkout`` is ``root`` for the main checkout, else the linked worktree;
    None when only the repository is known, such as for a retained worktree.
    """

    root: Path
    checkout: Path | None


def locate_checkout(cwd: Path) -> Checkout | None:
    """The checkout and repository holding ``cwd``, or None outside one."""
    try:
        resolved = cwd.expanduser().resolve()
        if (managed := ManagedWorktree.at(resolved)) is not None and (
            mapping := managed.retained_repository_mapping(resolved)
        ) is not None:
            return Checkout(root=mapping.root, checkout=None)
        if not resolved.is_dir():
            return None
        for folder in (resolved, *resolved.parents):
            marker = folder / ".git"
            if marker.is_dir():
                return Checkout(root=folder, checkout=folder)
            if marker.is_file():
                return Checkout(
                    root=_linked_checkout_root(folder, marker), checkout=folder
                )
    # A marker that is not UTF-8 (`ValueError`) leaves the folder unplaced
    # rather than failing a whole session list.
    except (OSError, ValueError):
        return None
    return None


def repository_root(cwd: Path) -> Path | None:
    """The main checkout of the repository holding ``cwd``, or None outside one."""
    found = locate_checkout(cwd)
    return found.root if found is not None else None


def checkouts(cwds: Iterable[str]) -> dict[str, Checkout | None]:
    """``locate_checkout`` of each folder, each one looked up once."""
    found: dict[str, Checkout | None] = {}
    for cwd in cwds:
        if cwd not in found:
            found[cwd] = locate_checkout(Path(cwd))
    return found


def repository_roots(cwds: Iterable[str]) -> dict[str, str | None]:
    """``repository_root`` of each folder, each one looked up once."""
    return {
        cwd: str(found.root) if found is not None else None
        for cwd, found in checkouts(cwds).items()
    }


# Sibling folders a guess reads at most: a parent holding more, such as `/tmp`,
# is no worktrees folder.
_SIBLINGS_READ = 64


def guess_gone_checkout(
    cwd: Path, scanned: dict[Path, Path | None] | None = None
) -> Checkout | None:
    """The repository a folder gone from disk most likely belonged to.

    Worktrees of one repository usually sit side by side (`worktrees/<repo>/<branch>`).
    When the closest folder still on disk holds checkouts that all share one
    repository, the gone folder was one of them: the checkout is the
    folder under that parent on the way to ``cwd``. Anything less clear-cut,
    such as `/tmp` holding checkouts of several repositories, is no guess.

    ``scanned`` keeps each parent's answer, so the folders of one listing
    that share a parent read its siblings once.
    """
    try:
        resolved = cwd.expanduser().absolute()
        parent = next((folder for folder in resolved.parents if folder.is_dir()), None)
        if parent is None or parent == resolved:
            return None
        if scanned is None:
            scanned = {}
        if parent not in scanned:
            scanned[parent] = _siblings_repository(parent)
        root = scanned[parent]
        if root is None:
            return None
        checkout = parent / resolved.relative_to(parent).parts[0]
        return Checkout(root=root, checkout=checkout)
    except (OSError, ValueError):
        return None


def guess_gone_checkouts(cwds: Iterable[str]) -> dict[str, Checkout | None]:
    """``guess_gone_checkout`` of each folder, each parent scanned once."""
    scanned: dict[Path, Path | None] = {}
    return {cwd: guess_gone_checkout(Path(cwd), scanned) for cwd in cwds}


def _siblings_repository(parent: Path) -> Path | None:
    """The one repository the checkouts in ``parent`` share, if any."""
    if locate_checkout(parent) is not None:
        return None
    siblings = list(itertools.islice(parent.iterdir(), _SIBLINGS_READ + 1))
    if len(siblings) > _SIBLINGS_READ:
        return None
    roots: set[Path] = set()
    linked = False
    for sibling in siblings:
        marker = sibling / ".git"
        if marker.is_dir():
            roots.add(sibling.resolve())
        elif marker.is_file():
            roots.add(_linked_checkout_root(sibling, marker))
            linked = True
    # A main checkout alone says nothing about the folders beside it.
    if len(roots) != 1 or not linked:
        return None
    return roots.pop()


def _linked_checkout_root(folder: Path, marker: Path) -> Path:
    """A ``.git`` file names the git dir: a linked worktree's shares the main
    checkout's through ``commondir``; a submodule's has none and is its own.
    """
    first_line = marker.read_text(encoding="utf-8").splitlines()[:1]
    if not first_line or not first_line[0].startswith(_GITDIR_PREFIX):
        return folder
    git_dir = (folder / first_line[0].removeprefix(_GITDIR_PREFIX).strip()).resolve()
    common_file = git_dir / "commondir"
    if not common_file.is_file():
        return folder
    common = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
    # A bare clone (`alpha.git`) is its own repository; its folder holds others.
    return common.parent if common.name == ".git" else common


__all__ = [
    "Checkout",
    "checkouts",
    "guess_gone_checkout",
    "guess_gone_checkouts",
    "locate_checkout",
    "repository_root",
    "repository_roots",
]
