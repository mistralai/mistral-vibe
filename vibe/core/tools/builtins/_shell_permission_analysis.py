from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from tree_sitter import Language, Node, Parser
import tree_sitter_bash as tsbash

_SUPPORTED_COMMAND_PARTS = {
    "command_name",
    "number",
    "word",
    "string",
    "raw_string",
    "concatenation",
}

_REDIRECTION_NODES = {
    "file_redirect",
    "heredoc_redirect",
    "herestring_redirect",
    "redirected_statement",
}

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


def _node_approval_reason(node: Node) -> str | None:
    if node.type in _REDIRECTION_NODES:
        return "redirection"
    if reason := _DYNAMIC_NODES.get(node.type):
        return reason
    if reason := _zsh_sensitive_node_reason(node):
        return reason
    if node.type == "concatenation" and any(
        child.type == "word" and child.text in {b"{", b"}"} for child in node.children
    ):
        return "brace expansion"
    if compound := _COMPOUND_NODES.get(node.type):
        return compound
    return "background execution" if node.type == "&" else None


@dataclass(frozen=True)
class ShellPermissionAnalysis:
    command_parts: tuple[str, ...]
    approval_reasons: tuple[str, ...]

    @property
    def requires_approval(self) -> bool:
        return bool(self.approval_reasons)

    @property
    def approval_label(self) -> str:
        """Prompt text naming what made the command unsafe to auto-approve."""
        return f"shell syntax requiring approval: {', '.join(self.approval_reasons)}"


@lru_cache(maxsize=1)
def _get_parser() -> Parser:
    return Parser(Language(tsbash.language()))


def analyze_shell_command(command: str) -> ShellPermissionAnalysis:
    """Extract commands and fail closed on syntax the policy cannot model."""
    tree = _get_parser().parse(command.encode("utf-8"))
    commands: list[str] = []
    approval_reasons: set[str] = set()

    if tree.root_node.has_error:
        approval_reasons.add("a syntax error")

    def find_commands(node: Node) -> None:
        if reason := _node_approval_reason(node):
            approval_reasons.add(reason)

        if node.type == "command":
            parts: list[str] = []
            for child in node.children:
                if part := _supported_command_part(child):
                    parts.append(part)
                elif child.type == "ansi_c_string":
                    # Preserve the token for guardrails such as find's execution
                    # predicate while requiring approval because shlex does not
                    # decode Bash ANSI-C quoting.
                    if child.text is not None:
                        parts.append(child.text.decode("utf-8"))
                elif child.type == "variable_assignment":
                    pass
                elif (
                    child.type not in _DYNAMIC_NODES
                    and not _zsh_sensitive_node_reason(child)
                ):
                    # The executor receives the original shell string. If policy
                    # extraction omits a semantic command child, the two views can
                    # differ, so the command must not be auto-approved. The node
                    # type is kept in the reason so an unexpected construct is
                    # identifiable from the approval prompt alone. Children already
                    # named by _DYNAMIC_NODES are skipped here because the walk
                    # records their specific reason when it recurses into them.
                    approval_reasons.add(f"unsupported syntax ({child.type})")

            # A redirect is a sibling of the command under redirected_statement.
            # Keep the marker so standalone-command denylist behavior stays intact.
            if parts and node.parent and node.parent.type == "redirected_statement":
                parts.append("<redirect>")
            if parts:
                commands.append(" ".join(parts))

        for child in node.children:
            find_commands(child)

    find_commands(tree.root_node)
    return ShellPermissionAnalysis(
        command_parts=tuple(commands), approval_reasons=tuple(sorted(approval_reasons))
    )
