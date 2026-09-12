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

_DYNAMIC_NODES = {
    "ansi_c_string": "ANSI-C quoted arguments require approval",
    "arithmetic_expansion": "shell arithmetic expansion requires approval",
    "brace_expression": "shell brace expansion requires approval",
    "command_substitution": "command substitution requires approval",
    "expansion": "shell parameter expansion requires approval",
    "process_substitution": "process substitution requires approval",
    "simple_expansion": "shell variable expansion requires approval",
    "variable_assignment": "environment assignments require approval",
}

_COMPOUND_NODES = {
    "case_statement",
    "compound_statement",
    "c_style_for_statement",
    "for_statement",
    "function_definition",
    "if_statement",
    "subshell",
    "while_statement",
}


@dataclass(frozen=True)
class ShellPermissionAnalysis:
    command_parts: tuple[str, ...]
    approval_reasons: tuple[str, ...]

    @property
    def requires_approval(self) -> bool:
        return bool(self.approval_reasons)


@lru_cache(maxsize=1)
def _get_parser() -> Parser:
    return Parser(Language(tsbash.language()))


def analyze_shell_command(command: str) -> ShellPermissionAnalysis:
    """Extract commands and fail closed on syntax the policy cannot model."""
    tree = _get_parser().parse(command.encode("utf-8"))
    commands: list[str] = []
    approval_reasons: set[str] = set()

    if tree.root_node.has_error:
        approval_reasons.add("shell syntax contains a parse error")

    def find_commands(node: Node) -> None:
        if node.type in _REDIRECTION_NODES:
            approval_reasons.add("shell redirection requires approval")
        if reason := _DYNAMIC_NODES.get(node.type):
            approval_reasons.add(reason)
        if node.type == "concatenation" and any(
            child.type == "word" and child.text in {b"{", b"}"}
            for child in node.children
        ):
            approval_reasons.add("shell brace expansion requires approval")
        if node.type in _COMPOUND_NODES:
            approval_reasons.add(f"shell {node.type} requires approval")
        if node.type == "&":
            approval_reasons.add("background execution requires approval")

        if node.type == "command":
            parts: list[str] = []
            for child in node.children:
                if child.type in _SUPPORTED_COMMAND_PARTS and child.text is not None:
                    parts.append(child.text.decode("utf-8"))
                elif child.type == "ansi_c_string":
                    # Preserve the token for guardrails such as find's execution
                    # predicate while requiring approval because shlex does not
                    # decode Bash ANSI-C quoting.
                    if child.text is not None:
                        parts.append(child.text.decode("utf-8"))
                elif child.type == "variable_assignment":
                    pass
                else:
                    # The executor receives the original shell string. If policy
                    # extraction omits a semantic command child, the two views can
                    # differ, so the command must not be auto-approved.
                    approval_reasons.add(
                        f"unsupported shell command syntax: {child.type}"
                    )

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
