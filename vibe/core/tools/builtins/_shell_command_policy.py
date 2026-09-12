from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShellCommandPolicy:
    requires_approval: bool = False
    inspect_positional_paths: bool = False
    option_path_values: tuple[str, ...] = ()


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


def _long_option_values(args: list[str], options: frozenset[str]) -> tuple[str, ...]:
    option_tokens = _option_tokens(args)
    values: list[str] = []
    for index, token in enumerate(option_tokens):
        _, separator, attached_value = token.partition("=")
        if not any(_matches_long_option(token, option) for option in options):
            continue
        if separator and attached_value:
            values.append(attached_value)
        elif not separator and index + 1 < len(option_tokens):
            values.append(option_tokens[index + 1])
    return tuple(values)


def _short_option_values(
    args: list[str], options: frozenset[str], *, preceding_value_options: frozenset[str]
) -> tuple[str, ...]:
    option_tokens = _option_tokens(args)
    values: list[str] = []
    for token_index, token in enumerate(option_tokens):
        if not token.startswith("-") or token.startswith("--"):
            continue
        for option_index, option in enumerate(token[1:]):
            if option in options:
                attached_value = token[option_index + 2 :]
                if attached_value:
                    values.append(attached_value)
                elif token_index + 1 < len(option_tokens):
                    values.append(option_tokens[token_index + 1])
                break
            if option in preceding_value_options:
                break
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
        option_path_values=_long_option_values(args, frozenset({"--random-source"})),
    )


def _grep_policy(args: list[str]) -> ShellCommandPolicy:
    return ShellCommandPolicy(
        option_path_values=(
            *_long_option_values(args, frozenset({"--file"})),
            *_short_option_values(
                args,
                frozenset({"f"}),
                preceding_value_options=frozenset({"A", "B", "C", "D", "d", "e", "m"}),
            ),
        )
    )


def _file_policy(args: list[str]) -> ShellCommandPolicy:
    files_from = (
        *_long_option_values(args, frozenset({"--files-from"})),
        *_short_option_values(
            args, frozenset({"f"}), preceding_value_options=frozenset({"e", "F", "P"})
        ),
    )
    magic_files = (
        *_long_option_values(args, frozenset({"--magic-file"})),
        *_short_option_values(
            args, frozenset({"m"}), preceding_value_options=frozenset({"e", "F", "P"})
        ),
    )
    return ShellCommandPolicy(
        option_path_values=files_from
        + tuple(path for value in magic_files for path in value.split(":"))
    )


def _files0_from_policy(args: list[str]) -> ShellCommandPolicy:
    return ShellCommandPolicy(
        option_path_values=_long_option_values(args, frozenset({"--files0-from"}))
    )


def _date_policy(args: list[str]) -> ShellCommandPolicy:
    # --set/-s writes the system clock. -I takes an optional attached value, so it
    # terminates a short cluster and keeps `date -Iseconds` out of the -s match.
    requires_approval = any(
        _matches_long_option(token, "--set")
        or _contains_short_option(
            token,
            frozenset({"s"}),
            preceding_value_options=frozenset({"d", "f", "I", "r"}),
        )
        for token in _option_tokens(args)
    )
    return ShellCommandPolicy(
        requires_approval=requires_approval,
        option_path_values=(
            *_long_option_values(args, frozenset({"--file"})),
            *_short_option_values(
                args, frozenset({"f"}), preceding_value_options=frozenset({"d", "s"})
            ),
        ),
    )


def _diff_policy(args: list[str]) -> ShellCommandPolicy:
    return ShellCommandPolicy(
        option_path_values=_long_option_values(
            args, frozenset({"--from-file", "--to-file"})
        )
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
    "date": _date_policy,
    "diff": _diff_policy,
    "du": _files0_from_policy,
    "file": _file_policy,
    "find": _find_policy,
    "git": _git_policy,
    "grep": _grep_policy,
    "less": _less_policy,
    "sort": _sort_policy,
    "tree": _tree_policy,
    "wc": _files0_from_policy,
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
    candidates = list(policy.option_path_values)
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
