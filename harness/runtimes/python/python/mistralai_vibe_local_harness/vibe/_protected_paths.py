"""Paths that decide what this session executes next.

Writing one converts a single approved call into arbitrary later execution, so the
Runtime escalates it whatever the classifier says. This is the one judgement the
gate does not delegate to a model.

It is a heuristic, not a boundary. It reads the argument text, never a resolved
target, so a symlink or a ``git config core.hooksPath /tmp/hooks`` write goes
unseen. What it buys is that the ordinary route to a hooks file reaches a human;
only an OS sandbox can make that a guarantee.

Deliberately not here: any analysis of what a shell command *does*. The Host
permission resolver owns that, with a tree-sitter parse and resolved paths this
module cannot see.
"""

from __future__ import annotations

from collections.abc import Iterator
import re

from mistralai_vibe_local_harness.session_protocol import JsonObject

# The membership rule is narrow: something the agent itself invokes must execute the
# file. ``.vibe`` holds our own config.toml and hooks.toml; the shell startup files
# are sourced by every bash call we make; git runs its hooks and honours
# core.hooksPath from .git/config every time we invoke it.
#
# Other agents' and editors' configuration is *not* here. ``.claude``, ``.cursor``,
# ``.codex``, ``.opencode``, ``.aider``, ``.idea``, ``.vscode`` and whatever ships
# next are an unbounded list nobody can finish, and nothing this agent runs executes
# them -- a different tool would, later, if the user starts it.
_PROTECTED_PATH = re.compile(
    r"(?<![\w.-])\.vibe(?![\w-])|"
    r"(?<![\w.-])\.git/(hooks|config)(?![\w-])|"
    r"(?<![\w.-])\.(bashrc|zshrc|bash_profile|zshenv|zprofile|profile)(?![\w-])",
    re.IGNORECASE,
)

# Builtins that modify a file they name in an argument. Reads are excluded: opening
# ``.vibe/config.toml`` decides nothing about what runs later, and prompting on it
# spends the user's attention on a non-event.
_WRITE_TOOLS = frozenset({"file_system.write_file", "file_system.search_replace"})
# Argument names carrying a path on those builtins.
_PATH_ARGUMENTS = ("path", "file_path")

# Spellings that reach the same file: a backslash separator, a doubled slash and a
# "." segment. All three only ever *join* what they sit between, so applying them
# can reveal a protected path but never hide one.
_JOINING_NOISE = (
    (re.compile(r"\\"), "/"),
    (re.compile(r"/{2,}"), "/"),
    (re.compile(r"/\.(?=/)"), ""),
)
# ``..`` is different in kind: it *cancels* the segment before it. That is what
# lets ``.git/foo/../hooks`` reach ``.git/hooks``, and also what lets a crafted
# operand delete the protected segment from the text we are about to search.
_PARENT_SEGMENT = re.compile(r"[^/]+/\.\.(?:/|$)")
# One command-line token. A path operand does not span whitespace.
_ARGV_SPAN = re.compile(r"\S+")


def _normalisations(text: str) -> Iterator[str]:
    """Every spelling of ``text`` this guard treats as reaching the same file.

    Yields rather than returning one answer because ``..`` cancellation is not
    confluent: a protected segment can exist in the middle of the rewrite and be
    gone by the end. ``re.sub`` also applies every non-overlapping match in a single
    pass, so two cancellations can happen at once and the segment between them never
    becomes a string anyone could inspect:

        .git/x/../hooks;/../tmp   --one sub()-->   .git/tmp

    So cancellation is applied one occurrence at a time and each step is yielded.
    The caller searches all of them, which makes the whole normalisation additive by
    construction: it can reveal a protected path, never conceal one. Earlier
    revisions instead tried to stop the erasure by narrowing what counts as a token
    -- first on whitespace, then on ``;``, ``&``, ``|`` -- which is an unbounded list
    and fixes one spelling per round.

    Terminates because each cancellation removes at least one character.
    """
    yield text
    for pattern, replacement in _JOINING_NOISE:
        previous = None
        while previous != text:
            previous = text
            text = pattern.sub(replacement, text)
    yield text
    while True:
        cancelled = _PARENT_SEGMENT.sub("", text, count=1)
        if cancelled == text:
            return
        text = cancelled
        yield text


def _names_protected_path(text: str) -> bool:
    """Whether any spelling of ``text`` names a path that decides what runs next."""
    return any(_PROTECTED_PATH.search(form) for form in _normalisations(text))


def protected_target(tool_name: str, args: JsonObject) -> str | None:
    """The execution-deciding path this call names, or ``None``.

    Two strengths, because the tools differ. A write builtin states its path as an
    argument, so the answer is exact. ``bash`` hands over a string and we do not
    parse it: any token that mentions a protected path counts. That over-matches --
    ``grep zshrc notes.txt`` is caught -- and the cost is one approval prompt, which
    is the right side to be wrong on.
    """
    if tool_name == "file_system.bash":
        command = args.get("command")
        if not isinstance(command, str):
            return None
        if not any(
            _names_protected_path(token) for token in _ARGV_SPAN.findall(command)
        ):
            return None
        return command
    if tool_name not in _WRITE_TOOLS:
        return None
    for name in _PATH_ARGUMENTS:
        value = args.get(name)
        if isinstance(value, str) and _names_protected_path(value):
            return value
    return None


__all__ = ["protected_target"]
