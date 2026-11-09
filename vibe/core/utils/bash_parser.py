from __future__ import annotations

from collections.abc import Collection
from functools import lru_cache

from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash


@lru_cache(maxsize=1)
def bash_parser() -> Parser:
    """The process-wide tree-sitter-bash parser.

    It reads bash, not zsh or POSIX sh, and ends a heredoc on an indented or
    padded delimiter line where bash does not.
    """
    return Parser(Language(tsbash.language()))


def walk(root: Node, *, skip: Collection[str] = ()) -> list[Node]:
    """``root`` and its descendants in source order, without ``skip`` subtrees."""
    nodes: list[Node] = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.type in skip:
            continue
        nodes.append(node)
        pending.extend(reversed(node.children))
    return nodes
