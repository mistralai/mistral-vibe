from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class ShellCommandPolicy:
    requires_approval: bool = False
    inspect_positional_paths: bool = False
    inspect_git_repository: bool = False
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
    side_effecting_long = {
        "--compress-program",
        "--files0-from",
        "--output",
        "--temporary-directory",
    }
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
            *_long_option_values(args, frozenset({"--exclude-from", "--file"})),
            *_short_option_values(
                args,
                frozenset({"f"}),
                preceding_value_options=frozenset({"A", "B", "C", "D", "d", "e", "m"}),
            ),
        )
    )


def _file_policy(args: list[str]) -> ShellCommandPolicy:
    options = _option_tokens(args)
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
        requires_approval=any(
            any(
                _matches_long_option(token, option)
                for option in {
                    "--compile",
                    "--files-from",
                    "--uncompress",
                    "--uncompress-noreport",
                }
            )
            or _contains_short_option(
                token,
                frozenset({"C", "f", "z", "Z"}),
                preceding_value_options=frozenset({"e", "F", "m", "P"}),
            )
            for token in options
        ),
        option_path_values=files_from
        + tuple(path for value in magic_files for path in value.split(":")),
    )


def _files0_from_policy(args: list[str]) -> ShellCommandPolicy:
    values = _long_option_values(args, frozenset({"--files0-from"}))
    return ShellCommandPolicy(
        requires_approval=any(
            _matches_long_option(token, "--files0-from")
            for token in _option_tokens(args)
        ),
        option_path_values=values,
    )


def _du_policy(args: list[str]) -> ShellCommandPolicy:
    files0_from = _long_option_values(args, frozenset({"--files0-from"}))
    exclude_from = (
        *_long_option_values(args, frozenset({"--exclude-from"})),
        *_short_option_values(
            args, frozenset({"X"}), preceding_value_options=frozenset({"B", "d", "t"})
        ),
    )
    return ShellCommandPolicy(
        requires_approval=any(
            _matches_long_option(token, "--files0-from")
            for token in _option_tokens(args)
        ),
        option_path_values=files0_from + exclude_from,
    )


_BSD_DATE_COMPONENT_LENGTHS = frozenset({2, 4, 6, 8, 10, 12})
_BSD_DATE_SECONDS_LENGTH = 2


def _date_short_option_state(token: str) -> tuple[bool, bool, bool]:
    """Return -j seen, -f seen, and whether the next argument is consumed."""
    has_no_set = False
    has_input_format = False
    value_options = frozenset({"d", "f", "r", "v", "z"})
    for index, option in enumerate(token[1:]):
        has_no_set = has_no_set or option == "j"
        has_input_format = has_input_format or option == "f"
        if option == "I":
            break
        if option in value_options:
            return has_no_set, has_input_format, index + 2 == len(token)
    return has_no_set, has_input_format, False


def _matches_bsd_date_setting_operand(value: str) -> bool:
    date_part, separator, seconds = value.partition(".")
    return (
        date_part.isdigit()
        and len(date_part) in _BSD_DATE_COMPONENT_LENGTHS
        and (
            not separator
            or (len(seconds) == _BSD_DATE_SECONDS_LENGTH and seconds.isdigit())
        )
    )


def _date_has_setting_operand(args: list[str]) -> bool:
    """Detect BSD date's positional clock-setting forms.

    GNU date has no valid non-option operand other than an output format, so
    conservatively applying this grammar on every POSIX platform only turns
    otherwise-invalid GNU invocations into approval prompts.
    """
    has_no_set = False
    has_input_format = False
    positional: list[str] = []
    skip_next = False
    options_ended = False
    long_value_options = frozenset({"--date", "--file", "--reference", "--rfc-3339"})

    for token in args:
        if skip_next:
            skip_next = False
            continue
        if options_ended:
            if not token.startswith("+"):
                positional.append(token)
            continue
        if token == "--":
            options_ended = True
            continue
        if token.startswith("+"):
            continue
        if token.startswith("--"):
            option, separator, _value = token.partition("=")
            if not separator and option in long_value_options:
                skip_next = True
            continue
        if token.startswith("-") and token != "-":
            token_no_set, token_input_format, skip_next = _date_short_option_state(
                token
            )
            has_no_set = has_no_set or token_no_set
            has_input_format = has_input_format or token_input_format
            continue
        positional.append(token)

    if has_no_set or not positional:
        return False
    if has_input_format:
        return True

    # BSD's positional setter uses [[[[[cc]yy]mm]dd]HH]MM[.ss]. Avoid asking
    # for arbitrary invalid GNU operands merely because they are positional.
    return any(_matches_bsd_date_setting_operand(value) for value in positional)


def _date_policy(args: list[str]) -> ShellCommandPolicy:
    # --set/-s writes the system clock. -I takes an optional attached value, so it
    # terminates a short cluster and keeps `date -Iseconds` out of the -s match.
    requires_approval = _date_has_setting_operand(args) or any(
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
        option_path_values=(
            *_long_option_values(
                args, frozenset({"--exclude-from", "--from-file", "--to-file"})
            ),
            *_short_option_values(
                args,
                frozenset({"X"}),
                preceding_value_options=frozenset({
                    "C",
                    "D",
                    "F",
                    "I",
                    "L",
                    "S",
                    "U",
                    "W",
                    "x",
                }),
            ),
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
            token in side_effecting_predicates or token == "-files0-from"
            for token in _option_tokens(args)
        )
    )


def _checksum_policy(args: list[str]) -> ShellCommandPolicy:
    return ShellCommandPolicy(
        requires_approval=any(
            _matches_long_option(token, "--check")
            or _contains_short_option(
                token, frozenset({"c"}), preceding_value_options=frozenset({"a"})
            )
            for token in _option_tokens(args)
        )
    )


_LESS_LONG_VALUE_OPTIONS = frozenset({
    "--autosave",
    "--buffers",
    "--cmd",
    "--color",
    "--emouse",
    "--end-prompt",
    "--jump-target",
    "--lesskey-content",
    "--lesskey-context",
    "--lesskey-file",
    "--lesskey-src",
    "--log-file",
    "--max-back-scroll",
    "--max-forw-scroll",
    "--pattern",
    "--prompt",
    "--quotes",
    "--rscroll",
    "--shift",
    "--tabs",
    "--tag",
    "--tag-file",
    "--window",
})
_LESS_APPROVAL_LONG_OPTIONS = frozenset({
    "--autosave",
    "--cmd",
    "--lesskey-content",
    "--lesskey-context",
    "--lesskey-file",
    "--lesskey-src",
    "--log-file",
    "--tag",
    "--tag-file",
})
_LESS_SHORT_VALUE_OPTIONS = frozenset('"#DbhjkopPtTxyz')
_LESS_SHORT_NUMERIC_VALUE_OPTIONS = frozenset("#bhjxyz")
_LESS_LONG_NUMERIC_VALUE_OPTIONS = frozenset({
    "--buffers",
    "--header",
    "--jump-target",
    "--line-num-width",
    "--match-shift",
    "--max-back-scroll",
    "--max-forw-scroll",
    "--modelines",
    "--shift",
    "--status-col-width",
    "--tabs",
    "--wheel-lines",
    "--window",
})
_LESS_LONG_STRING_VALUE_OPTIONS = _LESS_LONG_VALUE_OPTIONS | frozenset({
    "--intr",
    "--search-options",
})
_LESS_APPROVAL_SHORT_OPTIONS = frozenset({"k", "o", "O", "t", "T"})


def _normalize_less_long_option(token: str) -> str:
    # less accepts ``--+name`` as its long reset/default spelling. String-valued
    # option handlers can still consume their supplied configuration value.
    if token.startswith("--+"):
        token = "--" + token[3:]
    option, separator, value = token.partition("=")
    return option.lower() + separator + value


def _less_startup_requires_approval(token: str) -> bool:
    command = token[2:] if token.startswith("++") else token[1:]
    if command in {"g", "G"} or (
        command and all("0" <= character <= "9" for character in command)
    ):
        return False
    if not (command.startswith(("/", "?")) and command.isprintable()):
        return True
    return _less_string_value_requires_approval(command[1:])


def _less_numeric_value_requires_approval(value: str) -> bool:
    """Check options that less resumes parsing after a numeric value."""
    index = 0
    if (
        len(value) > 1
        and value[0] == "-"
        and ("0" <= value[1] <= "9" or value[1] == ".")
    ):
        index = 1
    while index < len(value) and (
        "0" <= value[index] <= "9" or value[index] in {".", ","}
    ):
        index += 1
    suffix = value[index:]
    if not suffix:
        return False
    if not suffix.startswith(("-", "+")):
        suffix = "-" + suffix
    return _less_policy([suffix]).requires_approval


def _less_string_value_requires_approval(value: str) -> bool:
    """Check the option suffix after less's attached-string ``$`` terminator."""
    _, separator, suffix = value.partition("$")
    if not separator or not suffix:
        return False
    if not suffix.startswith(("-", "+")):
        suffix = "-" + suffix
    return _less_policy([suffix]).requires_approval


def _less_long_option_requires_approval(token: str) -> bool:
    if any(
        _matches_long_option(token, option) for option in _LESS_APPROVAL_LONG_OPTIONS
    ):
        return True
    option, separator, attached_value = token.partition("=")
    if not separator:
        return False
    if any(
        _matches_long_option(option, numeric_option)
        for numeric_option in _LESS_LONG_NUMERIC_VALUE_OPTIONS
    ):
        return _less_numeric_value_requires_approval(attached_value)
    if any(
        _matches_long_option(option, string_option)
        for string_option in _LESS_LONG_STRING_VALUE_OPTIONS
    ):
        return _less_string_value_requires_approval(attached_value)
    # New less releases may add string-valued options. Their attached values
    # share the `$` terminator grammar, so fail closed on a risky resumed suffix
    # even before the option name is added to the version-specific table.
    if "$" in attached_value:
        return _less_string_value_requires_approval(attached_value)
    return False


def _less_short_option_action(token: str) -> tuple[bool, bool]:
    """Return whether this token requires approval and consumes the next token."""
    index = 1
    while index < len(token):
        remainder = token[index:]
        if remainder.startswith("--"):
            normalized = _normalize_less_long_option(remainder)
            return _less_long_option_requires_approval(normalized), False

        option = token[index]
        if option == "$":
            index += 1
            continue
        if option == "+":
            return _less_startup_requires_approval(remainder), False
        if "0" <= option <= "9":
            return _less_numeric_value_requires_approval(remainder), False
        if option in _LESS_APPROVAL_SHORT_OPTIONS:
            return True, False
        if option not in _LESS_SHORT_VALUE_OPTIONS:
            index += 1
            continue
        attached_value = token[index + 1 :]
        if option in _LESS_SHORT_NUMERIC_VALUE_OPTIONS:
            requires_approval = bool(
                attached_value and _less_numeric_value_requires_approval(attached_value)
            )
        else:
            requires_approval = bool(
                attached_value and _less_string_value_requires_approval(attached_value)
            )
        return requires_approval, not attached_value
    return False, False


def _less_policy(args: list[str]) -> ShellCommandPolicy:
    if any(not argument.isprintable() for argument in args):
        return ShellCommandPolicy(requires_approval=True)

    for argument in args:
        _, separator, resumed = argument.partition("$")
        if not separator or not (resumed := resumed.lstrip()):
            continue
        if not resumed.startswith(("-", "+")):
            resumed = "-" + resumed
        if _less_policy([resumed]).requires_approval:
            return ShellCommandPolicy(requires_approval=True)

    skip_next = False
    option_tokens = [
        (token, argument == "--") for argument in args for token in argument.split()
    ]
    for token, ends_options in option_tokens:
        if skip_next:
            skip_next = False
            continue
        if token == "--" and ends_options:
            break
        if token.startswith("+") and _less_startup_requires_approval(token):
            return ShellCommandPolicy(requires_approval=True)
        if token.startswith("--"):
            normalized = _normalize_less_long_option(token)
            if _less_long_option_requires_approval(normalized):
                return ShellCommandPolicy(requires_approval=True)
            # Only exact, version-stable spellings may hide the following token.
            # An ambiguous abbreviation or an option unknown to an older less
            # release does not consume its apparent value, which can expose a
            # following -k/--lesskey-* option to less instead.
            if "=" not in normalized and normalized in _LESS_LONG_VALUE_OPTIONS:
                skip_next = True
            continue
        if not token.startswith("-"):
            continue
        requires_approval, skip_next = _less_short_option_action(token)
        if requires_approval:
            return ShellCommandPolicy(requires_approval=True)
    return ShellCommandPolicy()


def _tree_policy(args: list[str]) -> ShellCommandPolicy:
    requires_approval = any(
        _matches_long_option(token, "--output")
        # tree uses a custom parser which resumes scanning the same cluster
        # after options such as -H/-I/-L/-P/-T/-X. Any `o` in a short cluster
        # therefore reaches the output-file option.
        or (token.startswith("-") and not token.startswith("--") and "o" in token[1:])
        for token in _option_tokens(args)
    )
    return ShellCommandPolicy(
        requires_approval=requires_approval,
        inspect_positional_paths=True,
        option_path_values=_long_option_values(args, frozenset({"--gitfile"})),
    )


def _git_policy(args: list[str]) -> ShellCommandPolicy:
    if not args or args[0] not in {"diff", "log", "status"}:
        return ShellCommandPolicy()
    subcommand = args[0]
    subcommand_args = args[1:]
    options = _option_tokens(subcommand_args)
    risky_options = {
        "--ext-diff",
        "--help",
        "--output",
        "--remerge-diff",
        "--show-signature",
        "--textconv",
    }
    requires_approval = any(
        any(_matches_long_option(token, option) for option in risky_options)
        for token in options
    )
    diff_merge_values = {
        value.casefold()
        for value in _long_option_values(subcommand_args, frozenset({"--diff-merges"}))
    }
    requires_approval = requires_approval or bool(diff_merge_values & {"r", "remerge"})
    option_paths = _long_option_values(
        subcommand_args, frozenset({"--pathspec-from-file"})
    )
    if subcommand in {"diff", "log"}:
        option_paths += _short_option_values(
            subcommand_args, frozenset({"O"}), preceding_value_options=frozenset()
        )
    return ShellCommandPolicy(
        requires_approval=requires_approval,
        inspect_positional_paths=(subcommand == "diff" and "--no-index" in options),
        # Plain commands stay auto-approved for ordinary repositories. The
        # permission resolver separately checks local Git configuration for
        # executable helpers before granting ALWAYS.
        inspect_git_repository=True,
        option_path_values=option_paths,
    )


_GIT_CONFIG_ENTRY = re.compile(
    r"^(?P<key>[A-Za-z][A-Za-z0-9-]*)\s*(?:=\s*(?P<value>.*))?$"
)
_FALSE_GIT_CONFIG_VALUES = frozenset({"", "0", "false", "no", "off"})
_GIT_SUBCOMMAND_INDEX = 1
_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")


def _command_name(token: str) -> str:
    name = token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in _WINDOWS_EXECUTABLE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _git_config_paths(cwd: Path) -> tuple[Path, ...]:
    """Locate repository-owned config without invoking Git."""
    for directory in (cwd, *cwd.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return marker / "config", marker / "config.worktree"
        if (
            (directory / "HEAD").is_file()
            and (directory / "config").is_file()
            and (directory / "objects").is_dir()
            and (directory / "refs").is_dir()
        ):
            return directory / "config", directory / "config.worktree"
        if not marker.is_file():
            continue
        try:
            marker_value = marker.read_text(encoding="utf-8").strip()
        except OSError:
            return ()
        prefix, separator, value = marker_value.partition(":")
        if not separator or prefix.casefold() != "gitdir":
            return ()
        git_dir = Path(value.strip()).expanduser()
        if not git_dir.is_absolute():
            git_dir = directory / git_dir
        common_dir = git_dir
        common_dir_file = git_dir / "commondir"
        try:
            common_value = common_dir_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        else:
            common_dir = Path(common_value)
            if not common_dir.is_absolute():
                common_dir = git_dir / common_dir
        return common_dir / "config", git_dir / "config.worktree"
    return ()


def git_repository_identity(cwd: Path) -> str | None:
    """Return a stable identity for the repository governing ``cwd``.

    The worktree-specific Git directory is used instead of the checkout path so
    symlinked paths resolve to the same approval scope while linked worktrees,
    whose ``config.worktree`` files may execute different helpers, remain
    distinct.
    """
    config_paths = _git_config_paths(cwd)
    if not config_paths:
        return None
    try:
        return str(config_paths[-1].parent.resolve())
    except OSError:
        return str(config_paths[-1].parent.absolute())


def _git_config_entries(cwd: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for config_path in _git_config_paths(cwd):
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        section = ""
        for raw_line in lines:
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and "]" in line:
                section = (
                    line[1 : line.index("]")]
                    .split(maxsplit=1)[0]
                    .split(".", maxsplit=1)[0]
                    .casefold()
                )
                continue
            if match := _GIT_CONFIG_ENTRY.match(line):
                entries.append((
                    section,
                    match.group("key").casefold(),
                    (match.group("value") or "true").strip().strip('"'),
                ))
    return tuple(entries)


def _git_value_is_active(value: str) -> bool:
    return value.casefold() not in _FALSE_GIT_CONFIG_VALUES


def git_repository_requires_approval(tokens: list[str], *, cwd: Path) -> bool:
    """Whether an otherwise-benign Git reader can run repository-owned code."""
    policy = analyze_shell_command_policy(tokens)
    if not policy.inspect_git_repository or len(tokens) <= _GIT_SUBCOMMAND_INDEX:
        return False

    subcommand = tokens[_GIT_SUBCOMMAND_INDEX].casefold()
    entries = _git_config_entries(cwd)
    if any(
        # Includes can hide any of the executable settings checked below.
        section in {"include", "includeif"}
        or (section == "core" and key == "pager" and _git_value_is_active(value))
        or (section == "pager" and key == subcommand and _git_value_is_active(value))
        for section, key, value in entries
    ):
        return True

    if subcommand in {"diff", "status"}:
        if any(
            (section == "core" and key == "fsmonitor" and _git_value_is_active(value))
            or (
                section == "filter"
                and key in {"clean", "process"}
                and _git_value_is_active(value)
            )
            for section, key, value in entries
        ):
            return True

    if subcommand in {"diff", "log", "status"} and any(
        section == "diff"
        and key in {"command", "external", "textconv"}
        and _git_value_is_active(value)
        for section, key, value in entries
    ):
        return True

    if subcommand == "log":
        # Remerge output can invoke repository-owned merge drivers. A verifier
        # can likewise be activated by local options/config or a user's global
        # log.showSignature setting.
        return any(
            (section == "merge" and key == "driver" and _git_value_is_active(value))
            or (section == "gpg" and key == "program" and _git_value_is_active(value))
            for section, key, value in entries
        )

    return False


def _uniq_policy(args: list[str]) -> ShellCommandPolicy:
    operand_count_with_output = 2
    positional_count = 0
    skip_next = False
    options_ended = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if options_ended:
            positional_count += 1
            continue
        if token == "--":
            options_ended = True
            continue
        if token.startswith("--"):
            option, separator, _value = token.partition("=")
            if not separator and option in {
                "--check-chars",
                "--skip-chars",
                "--skip-fields",
            }:
                skip_next = True
            continue
        is_legacy_plus_option = token.startswith("+") and token[1:].isdigit()
        if (token.startswith("-") and token != "-") or is_legacy_plus_option:
            for index, option in enumerate(token[1:]):
                if option in {"f", "s", "w"}:
                    if index + 2 == len(token):
                        skip_next = True
                    break
            continue
        positional_count += 1
    return ShellCommandPolicy(
        requires_approval=positional_count >= operand_count_with_output
    )


_COMMAND_POLICIES = {
    "date": _date_policy,
    "diff": _diff_policy,
    "du": _du_policy,
    "file": _file_policy,
    "find": _find_policy,
    "git": _git_policy,
    "grep": _grep_policy,
    "less": _less_policy,
    "md5sum": _checksum_policy,
    "more": _less_policy,
    "sha1sum": _checksum_policy,
    "sha256sum": _checksum_policy,
    "shasum": _checksum_policy,
    "sort": _sort_policy,
    "tree": _tree_policy,
    "uniq": _uniq_policy,
    "wc": _files0_from_policy,
}


def analyze_shell_command_policy(tokens: list[str]) -> ShellCommandPolicy:
    """Describe option semantics that are unsafe to infer from token shape alone."""
    if not tokens:
        return ShellCommandPolicy()
    command = _command_name(tokens[0])
    policy = _COMMAND_POLICIES.get(command)
    return policy(tokens[1:]) if policy else ShellCommandPolicy()


# Policies that can set ``requires_approval``. The rest only nominate path
# candidates, which become OUTSIDE_DIRECTORY permissions of their own -- a scope
# no command pattern is ever matched against, so widening one costs them nothing.
_OPTION_GATED_COMMANDS = frozenset({
    "date",
    "du",
    "file",
    "find",
    "git",
    "less",
    "md5sum",
    "more",
    "sha1sum",
    "sha256sum",
    "shasum",
    "sort",
    "tree",
    "uniq",
    "wc",
})


def has_option_guardrails(tokens: list[str]) -> bool:
    return bool(tokens) and _command_name(tokens[0]) in _OPTION_GATED_COMMANDS


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

    command = _command_name(tokens[0])
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
