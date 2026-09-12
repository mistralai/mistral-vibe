from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShellCommandPolicy:
    requires_approval: bool = False
    inspect_positional_paths: bool = False
    embedded_path_values: tuple[str, ...] = ()


def _matches_long_option(token: str, option: str) -> bool:
    option_token = token.partition("=")[0]
    return option_token == option or (
        option_token.startswith("--")
        and option_token != "--"
        and option.startswith(option_token)
    )


def _contains_short_option(
    token: str, options: frozenset[str], *, preceding_value_options: frozenset[str]
) -> bool:
    if not token.startswith("-") or token.startswith("--"):
        return False
    for option in token[1:]:
        if option in options:
            return True
        if option in preceding_value_options:
            return False
    return False


def _embedded_long_option_values(
    args: list[str], options: frozenset[str]
) -> tuple[str, ...]:
    values: list[str] = []
    for token in _option_tokens(args):
        _, separator, value = token.partition("=")
        if (
            separator
            and value
            and any(_matches_long_option(token, option) for option in options)
        ):
            values.append(value)
    return tuple(values)


def _option_tokens(args: list[str]) -> tuple[str, ...]:
    try:
        end = args.index("--")
    except ValueError:
        return tuple(args)
    return tuple(args[:end])


def _sort_policy(args: list[str]) -> ShellCommandPolicy:
    options = _option_tokens(args)
    side_effecting_long = {"--compress-program", "--output", "--temporary-directory"}
    requires_approval = any(
        any(_matches_long_option(token, option) for option in side_effecting_long)
        or _contains_short_option(
            token,
            frozenset({"o", "T"}),
            preceding_value_options=frozenset({"k", "S", "t"}),
        )
        for token in options
    )
    return ShellCommandPolicy(
        requires_approval=requires_approval,
        embedded_path_values=_embedded_long_option_values(
            args, frozenset({"--random-source"})
        ),
    )


def _find_policy(args: list[str]) -> ShellCommandPolicy:
    side_effecting_predicates = {
        "-delete",
        "-exec",
        "-execdir",
        "-fls",
        "-fprint",
        "-fprintf",
        "-fprint0",
        "-ok",
        "-okdir",
    }
    return ShellCommandPolicy(
        requires_approval=any(
            token in side_effecting_predicates for token in _option_tokens(args)
        )
    )


def _less_policy(args: list[str]) -> ShellCommandPolicy:
    requires_approval = any(
        _matches_long_option(token, "--log-file")
        or _matches_long_option(token, "--LOG-FILE")
        or _contains_short_option(
            token,
            frozenset({"o", "O"}),
            preceding_value_options=frozenset("DbhjkpPtTxyz"),
        )
        for token in _option_tokens(args)
    )
    return ShellCommandPolicy(requires_approval=requires_approval)


def _tree_policy(args: list[str]) -> ShellCommandPolicy:
    requires_approval = any(
        _matches_long_option(token, "--output")
        or _contains_short_option(
            token,
            frozenset({"o"}),
            preceding_value_options=frozenset({"H", "I", "L", "P", "T", "X"}),
        )
        for token in _option_tokens(args)
    )
    return ShellCommandPolicy(
        requires_approval=requires_approval, inspect_positional_paths=True
    )


def _git_policy(args: list[str]) -> ShellCommandPolicy:
    if not args or args[0] not in {"diff", "log"}:
        return ShellCommandPolicy()
    subcommand = args[0]
    subcommand_args = args[1:]
    options = _option_tokens(subcommand_args)
    requires_approval = any(
        _matches_long_option(token, "--output") or token in {"--ext-diff", "--textconv"}
        for token in options
    )
    return ShellCommandPolicy(
        requires_approval=requires_approval,
        inspect_positional_paths=(subcommand == "diff" and "--no-index" in options),
    )


_COMMAND_POLICIES = {
    "find": _find_policy,
    "git": _git_policy,
    "less": _less_policy,
    "sort": _sort_policy,
    "tree": _tree_policy,
}


def analyze_shell_command_policy(tokens: list[str]) -> ShellCommandPolicy:
    """Describe option semantics that are unsafe to infer from token shape alone."""
    if not tokens:
        return ShellCommandPolicy()
    command = tokens[0].rsplit("/", 1)[-1]
    policy = _COMMAND_POLICIES.get(command)
    return policy(tokens[1:]) if policy else ShellCommandPolicy()


def path_candidates(
    tokens: list[str], *, inspect_positional_paths: bool
) -> tuple[str, ...]:
    """Return operands and known option values that may name filesystem paths."""
    if not tokens:
        return ()

    policy = analyze_shell_command_policy(tokens)
    candidates = list(policy.embedded_path_values)
    if not (inspect_positional_paths or policy.inspect_positional_paths):
        return tuple(candidates)

    command = tokens[0].rsplit("/", 1)[-1]
    options_ended = False
    for token in tokens[1:]:
        if token == "--":
            options_ended = True
            continue
        if not options_ended and token.startswith("-"):
            continue
        if command == "chmod" and token.startswith("+"):
            continue
        candidates.append(token)
    return tuple(candidates)
