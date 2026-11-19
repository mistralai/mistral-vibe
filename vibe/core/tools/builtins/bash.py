from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import glob
from pathlib import Path
import shlex
from typing import ClassVar, NamedTuple

from pydantic import BaseModel, Field, ValidationInfo, computed_field, field_validator

from vibe.core.config.shell_timeouts import shell_timeout_seconds
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.arity import build_session_pattern
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.builtins._shell_command_policy import (
    analyze_shell_command_policy,
    git_repository_identity,
    git_repository_requires_approval,
    has_option_guardrails,
    path_candidates,
)
from vibe.core.tools.builtins._shell_permission_analysis import (
    ShellArgument,
    ShellPermissionAnalysis,
    analyze_shell_command,
    shell_arguments,
    shell_read_commands,
)
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.utils import (
    DEFAULT_SENSITIVE_PATTERNS,
    ambient_workspace,
    is_path_within_workdir,
    is_stream_device_path,
    matches_sensitive_pattern,
    names_a_link,
    resolve_tool_path,
    shell_path_scope_root,
    ungranted_paths,
)
from vibe.core.utils.shell import uses_posix_shell
from vibe.core.workspace import Workspace
from vibe.permissions import PathGrantScope, path_grant_pattern
from vibe.utils.paths import normalize_windows_path


def _extract_commands(command: str) -> list[str]:
    return list(analyze_shell_command(command).command_parts)


_READ_ONLY_COMMANDS_WINDOWS = ["dir", "findstr", "more", "type", "ver", "where"]
_READ_ONLY_COMMANDS_POSIX = [
    "basename",
    "cat",
    "comm",
    "cut",
    "date",
    "diff",
    "dirname",
    "du",
    "file",
    "find",
    "fmt",
    "fold",
    "grep",
    "head",
    "join",
    "less",
    "ls",
    "md5sum",
    "more",
    "nl",
    "od",
    "paste",
    "pwd",
    "readlink",
    "sha1sum",
    "sha256sum",
    "shasum",
    "sort",
    "stat",
    "sum",
    "tac",
    "tail",
    "tr",
    "uname",
    "uniq",
    "wc",
    "which",
]


def default_read_only_commands() -> list[str]:
    return list(
        _READ_ONLY_COMMANDS_POSIX if uses_posix_shell() else _READ_ONLY_COMMANDS_WINDOWS
    )


def _get_default_allowlist() -> list[str]:
    common = ["cd", "echo", "git diff", "git log", "git status", "tree", "whoami"]
    return common + default_read_only_commands()


def _get_default_denylist() -> list[str]:
    common = ["gdb", "pdb", "passwd"]

    if not uses_posix_shell():
        return common + ["cmd /k", "powershell -NoExit", "pwsh -NoExit", "notepad"]

    return common + [
        "nano",
        "vim",
        "vi",
        "emacs",
        "bash -i",
        "sh -i",
        "zsh -i",
        "fish -i",
        "dash -i",
        "screen",
        "tmux",
    ]


def _get_default_denylist_standalone() -> list[str]:
    common = ["python", "python3", "ipython"]

    if not uses_posix_shell():
        return common + ["cmd", "powershell", "pwsh", "notepad"]

    return common + ["bash", "sh", "nohup", "vi", "vim", "emacs", "nano", "su"]


_MUTATING_PATH_COMMANDS = {"cd", "chmod", "chown", "cp", "mkdir", "mv", "rm", "touch"}

# Every command whose path arguments must be checked against the workdir
# boundary. This must stay a superset of the read-only allowlist: any command
# that can be auto-allowed (see _is_unconditionally_allowed) has to have its
# paths inspected first, otherwise `grep root /etc/passwd`, `od -c ~/.ssh/id_rsa`
# and friends would read outside the workdir without ever requiring the
# OUTSIDE_DIRECTORY permission.
_PATH_COMMANDS = _MUTATING_PATH_COMMANDS | set(_READ_ONLY_COMMANDS_POSIX)


def _split_command_tokens(command: str) -> list[str]:
    # Windows needs no special case: Git Bash honours backslash escapes like any
    # POSIX shell, so keeping them literal would read `\"` as an opening quote.
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _wrapped_guardrail_commands(command: str) -> list[str]:
    """Extract statically visible commands invoked by shell builtins."""
    tokens = _split_command_tokens(command)
    if not tokens:
        return []

    if tokens[0] == "eval":
        evaluated = " ".join(tokens[1:])
        if not evaluated:
            return []
        return list(analyze_shell_command(evaluated).command_parts)

    if tokens[0] != "exec":
        return []

    arguments = _exec_arguments(tokens)
    return [shlex.join(arguments)] if arguments else []


def _exec_arguments(tokens: list[str]) -> list[str]:
    """The exec operand boundary shared by command and sensitive-path discovery."""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token == "-a":
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(tokens):
        return []
    return tokens[index:]


def _expand_guardrail_commands(command_parts: list[str]) -> list[str]:
    """Expand shell wrappers without dropping repeated command occurrences.

    Repository guardrails depend on the directory reached at each occurrence,
    so equal command text cannot be deduplicated globally. The ancestry set only
    prevents a pathological wrapper expansion from cycling within one branch.
    """
    expanded: list[str] = []
    pending = [(part, frozenset()) for part in command_parts]
    while pending:
        part, ancestors = pending.pop(0)
        expanded.append(part)
        if part in ancestors:
            continue
        next_ancestors = ancestors | {part}
        pending.extend(
            (wrapped, next_ancestors) for wrapped in _wrapped_guardrail_commands(part)
        )
    return expanded


_WORKING_DIRECTORY_COMMANDS = {
    "cd",
    "chdir",
    "pushd",
    "push-location",
    "pushl",
    "set-location",
    "sl",
}
_WORKING_DIRECTORY_POP_COMMANDS = {"popd", "pop-location", "popl"}
_WORKING_DIRECTORY_TOKEN_COUNT = 2
_SHELL_GLOB_CHARACTERS = frozenset("*?[")


def _is_working_directory_command(command: str) -> bool:
    return command.casefold() in (
        _WORKING_DIRECTORY_COMMANDS | _WORKING_DIRECTORY_POP_COMMANDS
    )


def _update_guardrail_cwds(tokens: list[str], possible_cwds: set[Path]) -> bool:
    """Track every statically possible cwd; return whether it became unknown."""
    if not tokens:
        return False
    command = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if command in _WORKING_DIRECTORY_POP_COMMANDS:
        # The cwd set is monotonic, so a plain pop can only return to a location
        # already recorded by an earlier push. Named/option-bearing stacks are
        # not statically knowable and therefore fail closed.
        return len(tokens) != 1
    if command not in _WORKING_DIRECTORY_COMMANDS:
        return False
    if command in {"pushd", "push-location"} and len(tokens) == 1:
        # Swapping an existing directory stack cannot introduce a new path.
        return False
    if (
        len(tokens) != _WORKING_DIRECTORY_TOKEN_COUNT
        or tokens[1].startswith("-")
        or any(character in tokens[1] for character in _SHELL_GLOB_CHARACTERS)
    ):
        return True
    possible_cwds.update(
        resolve_tool_path(tokens[1], cwd) for cwd in tuple(possible_cwds)
    )
    return False


def _git_repository_permission_pattern(
    command: str, possible_cwds: set[Path], *, cwd_is_unknown: bool
) -> str:
    """Key a Git-reader approval to every repository it may inspect."""
    identities: set[str] = set()
    for cwd in possible_cwds:
        identity = git_repository_identity(cwd)
        if identity is None:
            try:
                identity = f"directory:{cwd.resolve()}"
            except OSError:
                identity = f"directory:{cwd.absolute()}"
        identities.add(identity)
    if cwd_is_unknown:
        identities.add("dynamic-directory")
    return f"{command} [git repositories: {' | '.join(sorted(identities))}]"


def _collect_outside_paths(
    command_parts: list[str],
    *,
    workspace: Workspace | None = None,
    scratchpad_dir: Path | None = None,
    grep_pattern_may_be_omitted: bool = False,
) -> dict[str, str]:
    """Collect canonical paths referenced outside the workdir.

    Maps each canonical path to the token that named it, the spelling a refusal
    reports. Iterates file-manipulating commands (see _PATH_COMMANDS) and
    inspects their arguments as candidate paths. Skips flags (-r, --recursive)
    and chmod mode strings (+x).

    Only invoked under POSIX-shell semantics (see resolve_permission), where "/"
    is a valid path separator — including Git Bash on Windows, whose paths can
    look like /c/Users/... even though os.sep is "\\" there. Git Bash also
    accepts backslash-separated Windows paths.
    """
    workspace = workspace or ambient_workspace()
    resolved_cwd = workspace.cwd

    def is_within_workdir(path: str) -> bool:
        return is_path_within_workdir(path, workspace=workspace)

    paths: dict[str, str] = {}
    for part in _expand_guardrail_commands(command_parts):
        tokens = _split_command_tokens(part)
        command = Path(tokens[0]).name if tokens else None
        if not command:
            continue
        for token in path_candidates(
            tokens,
            inspect_positional_paths=command in _PATH_COMMANDS,
            grep_pattern_may_be_omitted=grep_pattern_may_be_omitted,
        ):
            # Only consider tokens that look like paths
            if not (
                token.startswith("/")
                or token.startswith("~")
                or token.startswith(".")
                or "/" in token
                or "\\" in token
                or names_a_link(token, resolved_cwd)
            ):
                continue
            path_token = normalize_windows_path(token)
            if is_within_workdir(path_token) or is_stream_device_path(path_token):
                continue
            if is_scratchpad_path(path_token, scratchpad_dir=scratchpad_dir):
                continue
            resolved = resolve_tool_path(path_token, resolved_cwd)
            paths.setdefault(str(resolved), path_token)
    return paths


def _collect_outside_dirs(
    command_parts: list[str],
    *,
    workspace: Workspace | None = None,
    scratchpad_dir: Path | None = None,
) -> set[str]:
    """Compatibility helper returning parents for outside paths."""
    paths = _collect_outside_paths(
        command_parts, workspace=workspace, scratchpad_dir=scratchpad_dir
    )
    return {
        str(Path(path) if Path(path).is_dir() else Path(path).parent) for path in paths
    }


@dataclass(frozen=True)
class SensitiveReadPaths:
    files: set[str]
    requires_exact_command: bool = False


_POSIX_OPAQUE_INTERPRETERS = frozenset({
    "bash",
    "sh",
    "dash",
    "zsh",
    "ksh",
    "fish",
    "csh",
    "tcsh",
})
# Shell options such as dotglob, nocaseglob or extglob let a later glob match
# names Python's glob does not.
_GLOB_OPTION_COMMANDS = frozenset({"shopt", "setopt", "unsetopt"})
# Bracket forms Bash and Zsh expand that Python's glob reads differently.
_UNSUPPORTED_BRACKET_FORMS = ("[^", "[[:", "[[=", "[[.")


@dataclass(frozen=True)
class _WrapperGrammar:
    """Options a command-running wrapper accepts before the command it runs.

    An option outside the grammar, including an abbreviated long option, makes
    the wrapped command unknown rather than guessed.
    """

    short_flags: frozenset[str] = frozenset()
    short_values: frozenset[str] = frozenset()
    short_optional_values: frozenset[str] = frozenset()
    long_flags: frozenset[str] = frozenset()
    long_values: frozenset[str] = frozenset()
    long_optional_values: frozenset[str] = frozenset()
    numeric_short_option: bool = False
    leading_operands: int = 0
    skips_assignments: bool = False


_POSIX_COMMAND_WRAPPERS = {
    "env": _WrapperGrammar(
        short_flags=frozenset("0iv"),
        short_values=frozenset("u"),
        long_flags=frozenset({"--debug", "--ignore-environment", "--null"}),
        long_values=frozenset({"--unset"}),
        skips_assignments=True,
    ),
    "nice": _WrapperGrammar(
        short_values=frozenset("n"),
        long_values=frozenset({"--adjustment"}),
        numeric_short_option=True,
    ),
    "nohup": _WrapperGrammar(),
    "setsid": _WrapperGrammar(
        short_flags=frozenset("cfw"),
        long_flags=frozenset({"--ctty", "--fork", "--wait"}),
    ),
    "stdbuf": _WrapperGrammar(
        short_values=frozenset("eio"),
        long_values=frozenset({"--error", "--input", "--output"}),
    ),
    "time": _WrapperGrammar(
        short_flags=frozenset("apqv"),
        short_values=frozenset("of"),
        long_flags=frozenset({"--append", "--portability", "--quiet", "--verbose"}),
        long_values=frozenset({"--format", "--output"}),
    ),
    "timeout": _WrapperGrammar(
        short_flags=frozenset("fpv"),
        short_values=frozenset("ks"),
        long_flags=frozenset({"--foreground", "--preserve-status", "--verbose"}),
        long_values=frozenset({"--kill-after", "--signal"}),
        leading_operands=1,
    ),
    "xargs": _WrapperGrammar(
        short_flags=frozenset("0oprtx"),
        short_values=frozenset("adEIJLnPRsS"),
        short_optional_values=frozenset("eil"),
        long_flags=frozenset({
            "--exit",
            "--interactive",
            "--no-run-if-empty",
            "--null",
            "--open-tty",
            "--show-limits",
            "--verbose",
        }),
        long_values=frozenset({
            "--arg-file",
            "--delimiter",
            "--max-args",
            "--max-chars",
            "--max-procs",
            "--process-slot-var",
        }),
        long_optional_values=frozenset({"--eof", "--max-lines", "--replace"}),
    ),
}
# These wrappers append operands read from input to the command they run.
_INPUT_OPERAND_WRAPPERS = frozenset({"xargs"})


def _wrapper_long_option_width(token: str, grammar: _WrapperGrammar) -> int | None:
    name, separator, _ = token.partition("=")
    if name in grammar.long_optional_values or (
        name in grammar.long_flags and not separator
    ):
        return 1
    if name in grammar.long_values:
        return 1 if separator else 2
    return None


def _wrapper_option_width(token: str, grammar: _WrapperGrammar) -> int | None:
    """Tokens one wrapper option occupies, or ``None`` outside the grammar."""
    if token.startswith("--"):
        return _wrapper_long_option_width(token, grammar)
    if grammar.numeric_short_option and token[1:].isdigit():
        return 1
    for position, option in enumerate(token[1:], start=2):
        if option in grammar.short_flags:
            continue
        if option in grammar.short_optional_values:
            return 1
        if option in grammar.short_values:
            return 1 if position < len(token) else 2
        return None
    return 1


def _wrapped_command(tokens: list[str], grammar: _WrapperGrammar) -> list[str] | None:
    """The command a wrapper runs, or ``None`` when it cannot be read with certainty."""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            break
        width = _wrapper_option_width(token, grammar)
        if width is None:
            return None
        index += width
    index += grammar.leading_operands
    while grammar.skips_assignments and index < len(tokens) and "=" in tokens[index]:
        index += 1
    # An expansion before the command can split into further options or
    # into the command itself.
    if any(
        not isinstance(token, ShellArgument) or token.dynamic
        for token in tokens[1:index]
    ):
        return None
    return tokens[index:]


class _SensitiveCommand(NamedTuple):
    arguments: list[str]
    continuation_uncertain: bool = False
    operands_from_input: bool = False


def _posix_cwd_is_opaque(tokens: list[str]) -> bool:
    """Whether a command may move the shell to a directory it does not name."""
    command = Path(tokens[0]).name
    return (
        tokens[0] in {".", "source"}
        or (isinstance(tokens[0], ShellArgument) and tokens[0].dynamic)
        or (
            (command == "eval" or _is_working_directory_command(command))
            and any(
                not isinstance(token, ShellArgument) or token.dynamic
                for token in tokens[1:]
            )
        )
    )


def _sensitive_command_arguments(
    command_parts: list[str],
) -> tuple[list[_SensitiveCommand], bool]:
    commands: list[_SensitiveCommand] = []
    unknown = False
    pending = [_SensitiveCommand(shell_arguments(part)) for part in command_parts]
    while pending:
        queued = pending.pop(0)
        tokens = queued.arguments
        if not tokens:
            continue
        commands.append(queued)
        name = Path(tokens[0]).name
        if name in _POSIX_OPAQUE_INTERPRETERS:
            # Child interpreter operands are opaque. Their exact script can be
            # approved, but it cannot grant guessed files or a broader wrapper.
            unknown = True
        if name in _POSIX_COMMAND_WRAPPERS:
            wrapped = _wrapped_command(tokens, _POSIX_COMMAND_WRAPPERS[name])
            if wrapped is None:
                unknown = True
            elif wrapped:
                pending.insert(
                    0,
                    queued._replace(
                        arguments=wrapped,
                        operands_from_input=queued.operands_from_input
                        or name in _INPUT_OPERAND_WRAPPERS,
                    ),
                )
        elif tokens[0] == "exec":
            arguments = _exec_arguments(tokens)
            if arguments:
                # A successful exec replaces the shell. Its own operand can
                # be understood, but later outer reads may never execute.
                pending = [
                    following._replace(continuation_uncertain=True)
                    for following in pending
                ]
                pending.insert(0, queued._replace(arguments=arguments))
        elif tokens[0] == "builtin":
            pending.insert(
                0,
                queued._replace(
                    arguments=tokens[2:] if tokens[1:2] == ["--"] else tokens[1:]
                ),
            )
        elif tokens[0] == "command":
            arguments = tokens[1:]
            while arguments and arguments[0] in {"-p", "--"}:
                arguments = arguments[1:]
            if arguments and arguments[0] not in {"-v", "-V"}:
                pending.insert(0, queued._replace(arguments=arguments))
        elif tokens[0] == "eval":
            if any(
                not isinstance(token, ShellArgument) or token.dynamic
                for token in tokens[1:]
            ):
                unknown = True
            else:
                # eval reparses its decoded argument text; exec does not.
                # eval runs before the next outer command and can change its
                # working directory. Keep that order when binding operands.
                pending[0:0] = [
                    queued._replace(arguments=shell_arguments(part))
                    for part in shell_read_commands(" ".join(tokens[1:]))
                ]
    return commands, unknown


def _sensitive_read_targets(candidate: ShellArgument, cwd: Path) -> set[Path] | None:
    """Files a reader operand may open from ``cwd``, or ``None`` when unknowable."""
    unresolved = resolve_tool_path(candidate, cwd)
    if not candidate.glob_positions:
        return {unresolved}
    pattern = "".join(
        glob.escape(char)
        if char in "*?[" and index not in candidate.glob_positions
        else char
        for index, char in enumerate(candidate)
    )
    if any(form in pattern for form in _UNSUPPORTED_BRACKET_FORMS):
        return None
    return {
        Path(match).resolve()
        for match in glob.iglob(str(resolve_tool_path(pattern, cwd)), recursive=True)
    } or {unresolved}


def _sensitive_operand_files(
    candidate: ShellArgument,
    possible_cwds: set[Path],
    *,
    sensitive_patterns: list[str],
    scratchpad_dir: Path | None,
) -> set[str] | None:
    """Sensitive files one operand reads, or ``None`` when only the exact
    command can be approved.
    """
    targets: set[Path] = set()
    for cwd in possible_cwds:
        cwd_targets = _sensitive_read_targets(candidate, cwd)
        if cwd_targets is None:
            return None
        targets |= cwd_targets
    sensitive = {
        str(resolved)
        for resolved in targets
        if not is_scratchpad_path(str(resolved), scratchpad_dir=scratchpad_dir)
        and matches_sensitive_pattern(str(resolved), sensitive_patterns)
    }
    if sensitive and len(possible_cwds) > 1:
        # After a cd the operand may name a file in more than one directory.
        # Granting each would approve files the command never reads.
        return None
    return sensitive


def _collect_sensitive_files(
    command_parts: list[str],
    *,
    command_cwd: Path,
    sensitive_patterns: list[str],
    scratchpad_dir: Path | None = None,
    grep_pattern_may_be_omitted: bool = False,
) -> SensitiveReadPaths:
    """Return sensitive files consumed by statically understood readers.

    Operands are resolved from every directory the command may have reached by
    then, as the Git guardrail does. ``cd sub && cat notes`` stays automatic
    when ``notes`` is ordinary from each of them; ``cd sub && cat .env`` is
    approved as the exact command, since a file grant would have to guess which
    directory's ``.env`` runs.
    """
    sensitive_files: set[str] = set()
    if not sensitive_patterns:
        return SensitiveReadPaths(sensitive_files)
    read_commands, requires_exact_command = _sensitive_command_arguments(command_parts)
    possible_cwds = {command_cwd}
    cwd_is_unknown = False
    glob_options_changed = False
    for queued in read_commands:
        tokens = queued.arguments
        if not tokens:
            continue

        command = Path(tokens[0]).name
        cwd_is_unknown = (
            cwd_is_unknown
            or queued.continuation_uncertain
            or _posix_cwd_is_opaque(tokens)
            or _update_guardrail_cwds(tokens, possible_cwds)
        )
        glob_options_changed = glob_options_changed or command in _GLOB_OPTION_COMMANDS
        if command not in _READ_ONLY_COMMANDS_POSIX:
            continue
        if queued.operands_from_input:
            # xargs supplies this reader's file operands at run time.
            requires_exact_command = True
            continue

        path_tokens = tokens
        policy = analyze_shell_command_policy(path_tokens)
        if policy.reads_recursively:
            # A recursive reader can open sensitive files it never names.
            requires_exact_command = True
            continue
        option_paths = policy.option_path_values
        option_spans = {
            (path.source_index, path.source_positions)
            for path in option_paths
            if isinstance(path, ShellArgument)
        }
        option_sources = {source for source, _ in option_spans}

        for candidate in path_candidates(
            path_tokens,
            inspect_positional_paths=True,
            grep_pattern_may_be_omitted=grep_pattern_may_be_omitted,
        ):
            if (
                isinstance(candidate, ShellArgument)
                and candidate.source_index in option_sources
                and (candidate.source_index, candidate.source_positions)
                not in option_spans
            ):
                # An option already bound this occurrence to specific paths,
                # such as file's colon-separated magic files. Its unsplit word
                # is not an additional positional file operand.
                continue
            if (
                cwd_is_unknown
                or not isinstance(candidate, ShellArgument)
                or candidate.dynamic
                or (glob_options_changed and candidate.glob_positions)
            ):
                requires_exact_command = True
                continue
            operand_files = _sensitive_operand_files(
                candidate,
                possible_cwds,
                sensitive_patterns=sensitive_patterns,
                scratchpad_dir=scratchpad_dir,
            )
            if operand_files is None:
                requires_exact_command = True
            else:
                sensitive_files |= operand_files
    return SensitiveReadPaths(sensitive_files, requires_exact_command)


def _matches_pattern(command: str, pattern: str) -> bool:
    return command == pattern or command.startswith(pattern + " ")


def command_session_pattern(tokens: list[str]) -> tuple[str, bool]:
    """The pattern a grant on this command is recorded under, and whether it is literal.

    ``build_session_pattern`` stars everything past the command's name, which is
    where guardrailed options sit: ``git log *``, earned by an innocuous
    ``git log $REF``, would cover ``git log --ext-diff`` and the guardrail would
    never be asked. A guardrailed command therefore keeps its own text -- read as
    text, since that text can itself hold the glob characters it is escaping.
    """
    if has_option_guardrails(tokens):
        return " ".join(tokens), True
    return build_session_pattern(tokens), False


def scoped_command_parts(
    analysis: ShellPermissionAnalysis, command_parts: list[str]
) -> tuple[list[str], bool]:
    """The parts a grant may be recorded against, and whether to include allowlisted.

    Once the extract stops describing what runs, no part is offered: emitting
    ``git *`` for ``git $SUB`` beside the exact command hands the widening over
    anyway.

    Where it does describe it, allowlisted parts contribute too. The approval
    owed for unreadable syntax has to sit on the commands that carried it rather
    than ride on whatever else the call needed -- an outside-workdir glob, a
    shell override -- and the allowlist already grants those commands every
    argument it can read.
    """
    if analysis.invalidates_scope:
        return [], False
    return command_parts, analysis.requires_approval


def needs_exact_command_scope(
    analysis: ShellPermissionAnalysis, required: list[RequiredPermission]
) -> bool:
    """Whether the command as written is the only scope left to record.

    Never added beside a pattern that would have generalised: the exact text
    releases this call and no other, so the user would answer for the session
    and still be asked next time.

    ``required`` must hold only what the commands earned. A context permission
    is ``COMMAND_PATTERN``-scoped too, and counting one would read ``[[ -n $FOO
    ]]`` under a shell override as a command that came out scoped when nothing
    about it was recorded at all.
    """
    if analysis.invalidates_scope:
        return True
    return analysis.requires_approval and not any(
        rp.scope is PermissionScope.COMMAND_PATTERN for rp in required
    )


NATIVE_DEFAULT_TIMEOUT_SECONDS = 300
"""Seconds the native shell tools give a command when neither the call nor the
configuration says. Only these tools use it: the unified harness has its own default,
and an unset `default_timeout` is not passed to it."""


class BashToolConfig(BaseToolConfig):
    shell_tool_name: ClassVar[str] = "bash"
    """The `[tools.<name>]` table these settings come from, named in a refused
    timeout's message."""

    permission: ToolPermission = ToolPermission.ASK
    max_output_bytes: int = Field(
        default=16_000, description="Maximum bytes to capture from stdout and stderr."
    )
    default_timeout: int | None = Field(
        default=None,
        description=(
            "Seconds a command may run when the call gives no timeout. Set alone, it"
            " must be at most Vibe's own maximum (300). Unset, Vibe's own default"
            " applies."
        ),
    )
    max_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "Longest foreground wait one tool call may ask for, in seconds. Set it"
            " with `default_timeout`, or neither. Unset, Vibe's own maximum applies."
        ),
    )
    allowlist: list[str] = Field(
        default_factory=_get_default_allowlist,
        description="Command prefixes that are automatically allowed",
    )
    denylist: list[str] = Field(
        default_factory=_get_default_denylist,
        description="Command prefixes that are automatically denied",
    )
    denylist_standalone: list[str] = Field(
        default_factory=_get_default_denylist_standalone,
        description="Commands that are denied only when run without arguments",
    )
    sensitive_patterns: list[str] = Field(
        default=["sudo"],
        description="Command prefixes that always ASK regardless of arity approval.",
    )
    sensitive_file_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
        description="File globs that always require approval when read by shell tools.",
    )

    @field_validator("default_timeout", "max_timeout_seconds", mode="before")
    @classmethod
    def _read_shell_timeout(cls, value: object, info: ValidationInfo) -> object:
        """Read a timeout as the config check does, so a config that loads is never
        refused when the tool is used, and one it refuses is refused alike.
        """
        if value is None:
            return None
        return shell_timeout_seconds(cls.shell_tool_name, info.field_name or "", value)

    @property
    def native_default_timeout(self) -> int:
        """Seconds a native shell tool gives a command whose call has no timeout."""
        return self.default_timeout or NATIVE_DEFAULT_TIMEOUT_SECONDS


class BashArgs(BaseModel):
    command: str = Field(description="The shell command to execute")
    timeout: int | None = Field(
        default=None, description="Override the default command timeout in seconds."
    )


class CapturedShellResult(BaseModel):
    """Result of a shell that captures stdout and stderr as two separate pipes."""

    command: str
    shell: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""

    # `model_dump` of a tool result is the `post_tool` hook payload, so dropping
    # this key outright would break hooks that read `tool_output.returncode`.
    @computed_field(description="Deprecated alias for `exit_code`.")
    @property
    def returncode(self) -> int:
        return self.exit_code


class Bash(BaseTool[BashArgs, CapturedShellResult, BashToolConfig, BaseToolState]):
    shell_rollout: ClassVar[str | None] = "legacy"
    # Command prefixes and typed path grants are what a shell reads back.
    allowlist_scopes: ClassVar[frozenset[PermissionScope]] = frozenset({
        PermissionScope.COMMAND_PATTERN,
        PermissionScope.OUTSIDE_DIRECTORY,
    })

    @staticmethod
    def _build_command_required_permission(
        invocation_pattern: str,
        session_pattern: str,
        label: str,
        *,
        literal: bool = False,
    ) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern=invocation_pattern,
            session_pattern=session_pattern,
            label=label,
            literal=literal,
        )

    @staticmethod
    def _build_outside_directory_permission(
        path: str, supplied: str
    ) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern=path,
            session_pattern=path_grant_pattern(path, PathGrantScope.EXACT),
            label=f"outside workdir ({path})",
            path_scope_root=shell_path_scope_root(path),
            supplied_pattern=supplied,
        )

    @staticmethod
    def _build_sensitive_file_permission(path: str) -> RequiredPermission:
        return RequiredPermission(
            scope=PermissionScope.FILE_PATTERN,
            invocation_pattern=path,
            session_pattern=glob.escape(path),
            label="accessing sensitive files",
        )

    def _find_denylist_match(self, command: str) -> str | None:
        return next(
            (p for p in self.config.denylist if _matches_pattern(command, p)), None
        )

    def _is_standalone_denylisted(self, command: str) -> bool:
        parts = command.split()
        if not parts:
            return False
        base_command = parts[0]
        if len(parts) == 1:
            command_name = Path(base_command).name
            if command_name in self.config.denylist_standalone:
                return True
            if base_command in self.config.denylist_standalone:
                return True
        return False

    def _is_allowlisted(self, command: str) -> bool:
        return any(
            _matches_pattern(command, pattern) for pattern in self.config.allowlist
        )

    def _is_sensitive(self, command: str) -> bool:
        tokens = command.split()
        if not tokens:
            return False
        return tokens[0] in self.config.sensitive_patterns

    def _resolve_guardrail_permission(
        self, command_parts: list[str], *, command_cwd: Path
    ) -> PermissionContext | None:
        option_required_by_command: dict[str, RequiredPermission] = {}
        possible_cwds = {command_cwd}
        cwd_is_unknown = False

        for part in _expand_guardrail_commands(command_parts):
            if matched := self._find_denylist_match(part):
                return PermissionContext(
                    permission=ToolPermission.NEVER,
                    reason=f"Command denied: '{part}' matches denylist pattern '{matched}'. Do not attempt to run this command.",
                )
            if self._is_standalone_denylisted(part):
                return PermissionContext(
                    permission=ToolPermission.NEVER,
                    reason=f"Command denied: '{part}' is not allowed as a standalone command. Do not attempt to run this command.",
                )
            tokens = _split_command_tokens(part)
            cwd_is_unknown = (
                _update_guardrail_cwds(tokens, possible_cwds) or cwd_is_unknown
            )
            policy = analyze_shell_command_policy(tokens)
            repository_requires_approval = policy.inspect_git_repository and (
                cwd_is_unknown
                or any(
                    git_repository_requires_approval(tokens, cwd=cwd)
                    for cwd in possible_cwds
                )
            )
            if not (policy.requires_approval or repository_requires_approval):
                continue
            permission_pattern = part
            if policy.inspect_git_repository:
                permission_pattern = _git_repository_permission_pattern(
                    part, possible_cwds, cwd_is_unknown=cwd_is_unknown
                )
            option_required_by_command[part] = self._build_command_required_permission(
                invocation_pattern=permission_pattern,
                session_pattern=permission_pattern,
                label=part,
                literal=True,
            )

        if not option_required_by_command:
            return None
        return PermissionContext(
            permission=ToolPermission.ASK,
            required_permissions=list(option_required_by_command.values()),
        )

    def _is_unconditionally_allowed(
        self, command_parts: list[str], sensitive_files: set[str]
    ) -> bool:
        if any(self._is_sensitive(part) for part in command_parts):
            return False
        if sensitive_files:
            return False

        if self.config.permission == ToolPermission.ALWAYS:
            return True

        return all(self._is_allowlisted(part) for part in command_parts)

    def _build_required_permissions(
        self,
        command_parts: list[str],
        outside_paths: Mapping[str, str],
        sensitive_files: set[str],
        *,
        include_allowlisted: bool = False,
    ) -> list[RequiredPermission]:
        required: list[RequiredPermission] = []
        seen_session: set[str] = set()

        for part in command_parts:
            if not part:
                continue
            tokens = part.split()
            if not tokens:
                continue

            is_sensitive = self._is_sensitive(part)
            if (
                not is_sensitive
                and not include_allowlisted
                and self._is_allowlisted(part)
            ):
                continue

            if is_sensitive:
                required.append(
                    self._build_command_required_permission(
                        invocation_pattern=part,
                        session_pattern=part,
                        label=part,
                        literal=True,
                    )
                )
                continue

            session_pat, literal = command_session_pattern(tokens)
            if session_pat in seen_session:
                continue
            seen_session.add(session_pat)
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=part,
                    session_pattern=session_pat,
                    label=session_pat,
                    literal=literal,
                )
            )

        for path, supplied in sorted(outside_paths.items()):
            required.append(self._build_outside_directory_permission(path, supplied))
        for path in sorted(sensitive_files):
            required.append(self._build_sensitive_file_permission(path))

        return required

    def resolve_permission(self, args: BashArgs) -> PermissionContext | None:
        if not uses_posix_shell():
            return None

        analysis = analyze_shell_command(args.command)
        command_parts = list(analysis.command_parts)
        if not command_parts and not analysis.requires_approval:
            return None

        guardrail_permission = self._resolve_guardrail_permission(
            command_parts, command_cwd=self.workspace.cwd
        )
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission
        outside_paths = _collect_outside_paths(
            command_parts,
            workspace=self.workspace,
            scratchpad_dir=self.scratchpad_dir,
            grep_pattern_may_be_omitted=analysis.requires_approval,
        )
        sensitive_files = _collect_sensitive_files(
            shell_read_commands(args.command),
            command_cwd=self.workspace.cwd,
            sensitive_patterns=self.config.sensitive_file_patterns,
            scratchpad_dir=self.scratchpad_dir,
            grep_pattern_may_be_omitted=analysis.requires_approval,
        )
        outside_paths = ungranted_paths(
            outside_paths, self.config.allowlist, self.workspace
        )
        commands_cleared = (
            self._is_unconditionally_allowed(command_parts, sensitive_files.files)
            and not guardrail_permission
            and not analysis.requires_approval
            and not sensitive_files.requires_exact_command
        )
        if commands_cleared and not outside_paths:
            return PermissionContext(permission=ToolPermission.ALWAYS)

        # Cleared commands leave only the outside paths to answer for.
        scoped_parts, include_allowlisted = (
            ([], False)
            if commands_cleared or sensitive_files.requires_exact_command
            else scoped_command_parts(analysis, command_parts)
        )
        required = self._build_required_permissions(
            scoped_parts,
            outside_paths,
            sensitive_files.files,
            include_allowlisted=include_allowlisted,
        )
        if guardrail_permission:
            required.extend(guardrail_permission.required_permissions)
        if sensitive_files.requires_exact_command or needs_exact_command_scope(
            analysis, required
        ):
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=args.command,
                    session_pattern=args.command,
                    label=(
                        "dynamic sensitive-file read (bash)"
                        if sensitive_files.requires_exact_command
                        else analysis.approval_label
                    ),
                    literal=True,
                )
            )
        if not required:
            return None

        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )
