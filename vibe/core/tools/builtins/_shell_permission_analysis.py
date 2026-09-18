from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

from vibe.core.tools.arity import known_session_pattern_arity
from vibe.core.tools.builtins._shell_command_policy import has_option_guardrails

_SUPPORTED_COMMAND_PARTS = {
    "command_name",
    "number",
    "word",
    "string",
    "raw_string",
    "concatenation",
}

# ``redirected_statement`` is only a wrapper around a command and its redirects;
# every redirect kind it can hold is named here or handled by
# ``_file_redirect_reason``, so treating the wrapper itself as a reason would flag
# a command for redirecting at all rather than for what the redirect does.
_REDIRECTION_NODES = {"heredoc_redirect", "herestring_redirect"}

# Children of a ``file_redirect`` that name its target rather than its operator.
_REDIRECT_VALUE_NODES = {"word", "number", "string", "raw_string", "concatenation"}
# Operators that renumber a descriptor instead of opening a path.
_DUPLICATION_OPERATORS = {">&", "<&"}
# Writes here are discarded by the kernel, so the redirect reaches no file.
_DISCARD_TARGET = "/dev/null"


def _file_redirect_reason(node: Node) -> str | None:
    """``None`` when a redirect cannot reach a file, else the approval reason.

    ``2>&1`` renumbers a descriptor and ``>/dev/null`` discards, so neither can
    create, truncate, or read anything. Requiring approval for them charged a
    prompt to the ordinary "run it and drop the noise" shape. Every other redirect
    names a path the command may write or read, which is what approval is for.

    Each redirect is a separate node, so a command that mixes them still asks:
    ``cmd >out.txt 2>&1`` is approved on ``>out.txt``.
    """
    target = next(
        (
            child
            for child in reversed(node.children)
            if child.type in _REDIRECT_VALUE_NODES
        ),
        None,
    )
    if target is None or target.text is None:
        return "redirection"
    operators = {
        child.type
        for child in node.children
        if child.type not in _REDIRECT_VALUE_NODES and child.type != "file_descriptor"
    }
    # A duplication target must be a bare number: ``>&1`` renumbers, but ``>&file``
    # is Bash shorthand for redirecting both streams into that file.
    if operators <= _DUPLICATION_OPERATORS and target.type == "number":
        return None
    if target.text.decode("utf-8") == _DISCARD_TARGET:
        return None
    return "redirection"


# Reasons are noun phrases so they read as a list in the approval prompt.
_DYNAMIC_NODES = {
    "ansi_c_string": "ANSI-C quoted arguments",
    "arithmetic_expansion": "arithmetic expansion",
    "brace_expression": "brace expansion",
    "command_substitution": "command substitution",
    "expansion": "parameter expansion",
    "process_substitution": "process substitution",
    "simple_expansion": "variable expansion",
    "variable_assignment": "environment assignments",
}

_COMPOUND_NODES = {
    "case_statement": "case statement",
    "compound_statement": "command group",
    "c_style_for_statement": "for loop",
    "for_statement": "for loop",
    "function_definition": "function definition",
    "if_statement": "if statement",
    "subshell": "subshell",
    "test_command": "test expression",
    "while_statement": "while loop",
}


# Node types that make the parse an unreliable account of what will run, rather
# than syntax the walk can read around.
_UNFAITHFUL_PARSE_NODES = {"function_definition"}

_QUOTE_CHARS = frozenset("\"'")

# Commands whose argument is a program or script to run, so their arity boundary
# is about their own options rather than where their identity stops.
_OPAQUE_ARGUMENT_COMMANDS = frozenset({".", "env", "source"})


def _contains_dynamic(node: Node) -> bool:
    if node.type in _DYNAMIC_NODES:
        return True
    return any(_contains_dynamic(child) for child in node.children)


def _zsh_sensitive_word_reason(value: bytes) -> str | None:
    """Name Zsh word expansions that the Bash grammar treats as literals."""
    if value.startswith(b"="):
        return "Zsh equals expansion"
    if b"==" in value:
        return "Zsh magic-equals expansion"
    if value.startswith(b"~") and not (value == b"~" or value.startswith(b"~/")):
        return "shell-specific named-directory expansion"
    if b"***" in value:
        return "Zsh symlink-following glob"
    return None


def _zsh_sensitive_node_reason(node: Node) -> str | None:
    if node.type not in {"command_name", "word"} or node.text is None:
        return None
    return _zsh_sensitive_word_reason(node.text)


def _supported_command_part(node: Node) -> str | None:
    if node.type not in _SUPPORTED_COMMAND_PARTS or node.text is None:
        return None
    if _zsh_sensitive_node_reason(node):
        return None
    return node.text.decode("utf-8")


# Node types whose reason depends only on the type. ``_zsh_sensitive_node_reason``
# fires only on command_name/word, which none of these contain, so one lookup is
# equivalent to consulting each table in turn.
_FIXED_REASON_NODES = {
    **dict.fromkeys(_REDIRECTION_NODES, "redirection"),
    **_DYNAMIC_NODES,
    **_COMPOUND_NODES,
    "&": "background execution",
}


def _node_approval_reason(node: Node) -> str | None:
    if node.type == "file_redirect":
        return _file_redirect_reason(node)
    if reason := _FIXED_REASON_NODES.get(node.type):
        return reason
    if reason := _zsh_sensitive_node_reason(node):
        return reason
    if node.type == "concatenation" and any(
        child.type == "word" and child.text in {b"{", b"}"} for child in node.children
    ):
        return "brace expansion"
    return None


@dataclass(frozen=True)
class ShellPermissionAnalysis:
    command_parts: tuple[str, ...]
    approval_reasons: tuple[str, ...]
    invalidates_scope: bool = False
    """Whether ``command_parts`` have stopped describing what the shell will run.

    True where the syntax reaches the command's identity -- which program runs,
    which file a redirect opens -- so nothing narrower than the command text is
    honest to record. False where it only varies a known command's arguments.
    """

    @property
    def requires_approval(self) -> bool:
        return bool(self.approval_reasons)

    @property
    def approval_label(self) -> str:
        """Prompt text naming what made the command unsafe to auto-approve."""
        return f"shell syntax requiring approval: {', '.join(self.approval_reasons)}"


@dataclass(frozen=True)
class _CommandRead:
    text: str
    reasons: frozenset[str]
    invalidates_scope: bool


def _identity_survives(parts: list[str], first_unreadable: int) -> bool:
    """Whether a pattern over ``parts`` still names what an unreadable token runs.

    Only a boundary the arity table states counts, never the fallback one:
    ``sudo $CMD`` would otherwise read as an argument the trailing ``*`` covers
    when it is the program ``sudo *`` would go on to run.
    """
    # Re-split rather than assume one part per token: the tool layer cuts the
    # pattern off the same re-split, so both read the boundary off the same
    # tokens.
    tokens = " ".join(parts).split()
    if not tokens or tokens[0] in _OPAQUE_ARGUMENT_COMMANDS:
        return False
    # A guardrailed command's grant is recorded as its own text, with no trailing
    # ``*`` to wildcard the token away -- the extract simply loses it. And the
    # token could be the guarded option: ``git log $REF`` runs ``--ext-diff``
    # when ``REF`` says so, and ``git log --output $F`` reads the same however
    # ``F`` is pointed.
    if has_option_guardrails(tokens):
        return False
    arity = known_session_pattern_arity(tokens)
    if arity is None:
        return False
    # A quote in the kept prefix means the re-split landed mid-token, so neither
    # the boundary nor the pattern cut at it describes a real command.
    if any(_QUOTE_CHARS & set(token) for token in tokens[:arity]):
        return False
    return first_unreadable >= arity


def _read_command(node: Node) -> _CommandRead:
    """Extract one ``command`` node, and judge whether the extract can be granted.

    A token the extractor cannot read is harmless only where the session pattern
    would have wildcarded it away; any earlier and it was part of the command's
    identity, so ``git $SUB`` would leave ``git *``.
    """
    parts: list[str] = []
    reasons: set[str] = set()
    invalidates_scope = False
    first_unreadable: int | None = None

    for child in node.children:
        # A prefix assignment takes no argument position, so it shifts no index.
        # The enclosing walk judges that it is also unpatternable.
        if child.type == "variable_assignment":
            continue
        # Taken before the append so a child that is dropped still accounts for
        # the slot it occupied.
        index = len(" ".join(parts).split())
        part = _supported_command_part(child)
        if part is not None:
            parts.append(part)
            unreadable = _contains_dynamic(child)
        else:
            unreadable = True
            if child.type == "ansi_c_string":
                # Preserve the token for guardrails such as find's execution
                # predicate while requiring approval because shlex does not
                # decode Bash ANSI-C quoting.
                if child.text is not None:
                    parts.append(child.text.decode("utf-8"))
            elif child.type not in _DYNAMIC_NODES and not _zsh_sensitive_node_reason(
                child
            ):
                # The executor receives the original shell string, so a semantic
                # child this extraction omits makes the two views differ. The
                # node type rides along so the prompt alone identifies it.
                # _DYNAMIC_NODES children are skipped: the walk records their
                # own reason when it recurses into them.
                reasons.add(f"unsupported syntax ({child.type})")
                invalidates_scope = True
        if unreadable and first_unreadable is None:
            first_unreadable = index

    if first_unreadable is not None and not _identity_survives(parts, first_unreadable):
        invalidates_scope = True

    # A redirect is a sibling of the command under redirected_statement. Keep the
    # marker so standalone-command denylist behavior stays intact.
    if parts and node.parent and node.parent.type == "redirected_statement":
        parts.append("<redirect>")
    return _CommandRead(
        text=" ".join(parts),
        reasons=frozenset(reasons),
        invalidates_scope=invalidates_scope,
    )


@lru_cache(maxsize=1)
def _get_parser() -> Parser:
    return Parser(Language(tsbash.language()))


def analyze_shell_command(command: str) -> ShellPermissionAnalysis:
    """Extract commands and fail closed on syntax the policy cannot model."""
    tree = _get_parser().parse(command.encode("utf-8"))
    commands: list[str] = []
    approval_reasons: set[str] = set()
    invalidates_scope = False

    # POSIX shells remove backslash-newline pairs before tokenization. The Bash
    # grammar does not apply that preprocessing consistently, so policy can see
    # a different command from the one the shell ultimately executes.
    if "\\\n" in command:
        approval_reasons.add("line continuation")
        invalidates_scope = True

    if tree.root_node.has_error:
        approval_reasons.add("a syntax error")
        invalidates_scope = True

    def find_commands(node: Node) -> None:
        nonlocal invalidates_scope
        if reason := _node_approval_reason(node):
            approval_reasons.add(reason)

        if node.type == "variable_assignment":
            # ``HOME=/x git status`` and ``git status`` extract identically, so
            # ``git status *`` would cover any environment -- including one whose
            # gitconfig redirects what git runs.
            invalidates_scope = True
        elif node.type in _UNFAITHFUL_PARSE_NODES:
            invalidates_scope = True
        elif node.type in _REDIRECTION_NODES:
            # The body is what the command actually runs -- the script behind
            # ``python <<EOF`` -- and none of it reaches ``command_parts``, so
            # ``python <redirect> *`` would grant every script there is.
            invalidates_scope = True
        elif node.type == "file_redirect" and _file_redirect_reason(node) is not None:
            # The target is recorded as the ``<redirect>`` marker, which a
            # trailing ``*`` covers: the pattern would grant every target.
            invalidates_scope = True

        if node.type == "command":
            read = _read_command(node)
            approval_reasons.update(read.reasons)
            invalidates_scope = invalidates_scope or read.invalidates_scope
            if read.text:
                commands.append(read.text)

        for child in node.children:
            find_commands(child)

    find_commands(tree.root_node)
    return ShellPermissionAnalysis(
        command_parts=tuple(commands),
        approval_reasons=tuple(sorted(approval_reasons)),
        invalidates_scope=invalidates_scope,
    )
