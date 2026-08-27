from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe import VIBE_ROOT
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.base import (
    BaseTool,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.builtins.bash import (
    BashToolConfig,
    CapturedShellResult,
    completed_shell_result,
)
from vibe.core.tools.builtins.experimental_bash import (
    BashLogFile,
    BashLogFileArgs,
    BashLogFileResult,
    BashOutput,
    BashOutputArgs,
    BashOutputResult,
    BashSessions,
    BashSessionsArgs,
    BashSessionsResult,
    BashStdin,
    BashStdinArgs,
    BashStdinResult,
    ExperimentalBash,
    ExperimentalBashArgs,
    ExperimentalBashToolConfig,
    ManagedShellError,
    _BashPermissionMixin,
    _matches_pattern,
)
from vibe.core.tools.builtins.managed_shell import backend as managed_shell_backend
from vibe.core.tools.builtins.managed_shell._windows import (
    build_windows_shell_argv,
    git_bash_shell_available,
    resolve_powershell_shell,
)
from vibe.core.tools.builtins.managed_shell.backend import ManagedShellBackendError
from vibe.core.tools.io_port import ShellCommandRequest
from vibe.core.tools.permissions import PermissionContext, RequiredPermission
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import ToolPath, is_path_within_workdir, resolve_tool_path
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows, kill_async_subprocess
from vibe.core.workspace import Workspace
from vibe.observability.logging import logger
from vibe.utils.io import decode_console_safe
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


# Load-bearing: keeps the inherited args/result models in this module's globals so
# get_type_hints() can resolve the companion tools' annotations under
# `from __future__ import annotations`. Do not remove — see get_parameters().
_INHERITED_ANNOTATION_MODELS = (
    BashLogFileArgs,
    BashLogFileResult,
    BashOutputArgs,
    BashOutputResult,
    BashSessionsArgs,
    BashSessionsResult,
    BashStdinArgs,
    BashStdinResult,
)

WINDOWS_SESSION_PREFIX = "powershell"
_WINDOWS_DRIVE_PREFIX_LENGTH = 2
_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".ps1")
_WINDOWS_POWERSHELL_COMMAND_ALIASES = {
    "ac": "add-content",
    "cat": "get-content",
    "cd": "set-location",
    "chdir": "set-location",
    "copy": "copy-item",
    "cp": "copy-item",
    "cpi": "copy-item",
    "del": "remove-item",
    "dir": "get-childitem",
    "erase": "remove-item",
    "gc": "get-content",
    "gci": "get-childitem",
    "ls": "get-childitem",
    "md": "new-item",
    "mi": "move-item",
    "mkdir": "new-item",
    "move": "move-item",
    "mv": "move-item",
    "ni": "new-item",
    "rd": "remove-item",
    "ren": "rename-item",
    "rename": "rename-item",
    "ri": "remove-item",
    "rm": "remove-item",
    "rmdir": "remove-item",
    "rni": "rename-item",
    "sc": "set-content",
    "sl": "set-location",
    "sls": "select-string",
    "type": "get-content",
}
_WINDOWS_NON_PATH_COMMANDS = {"echo", "write-host", "write-output"}
_WINDOWS_SLASH_OPTION_COMMANDS = {
    "findstr",
    "more",
    "robocopy",
    "tree",
    "ver",
    "where",
    "whoami",
    "xcopy",
}


def _windows_managed_shell_enabled(config: VibeConfigSchema | None) -> bool:
    _ = config
    return bool(
        is_windows()
        and _powershell_treatment_available()
        and managed_shell_backend.managed_shell_supported("powershell")
    )


def powershell_shell_available() -> bool:
    if not is_windows():
        return False
    try:
        resolve_powershell_shell(None, None)
    except ManagedShellBackendError as exc:
        logger.info("PowerShell managed-shell treatment unavailable: %s", exc)
        return False
    return True


def _powershell_treatment_available() -> bool:
    return powershell_shell_available() and not git_bash_shell_available()


def _flush_windows_command_part(parts: list[str], buffer: list[str]) -> None:
    part = "".join(buffer).strip()
    if part:
        parts.append(part)
    buffer.clear()


def _find_powershell_group_end(
    command: str, opening_index: int, opening: str, closing: str
) -> int | None:
    depth = 1
    quote: str | None = None
    escaped = False
    index = opening_index + 1

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue

        if char == "`":
            escaped = True
            index += 1
            continue

        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            index += 1
            continue

        if quote == "'":
            index += 1
            continue

        if quote == '"':
            if command[index : index + 2] == "$(":
                nested_end = _find_powershell_group_end(command, index + 1, "(", ")")
                if nested_end is None:
                    return None
                index = nested_end + 1
                continue
            index += 1
            continue

        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1

    return None


def _find_powershell_subexpression_end(command: str, start: int) -> int | None:
    return _find_powershell_group_end(command, start + 1, "(", ")")


def _append_powershell_group(
    command: str, index: int, buffer: list[str], nested_expressions: list[str]
) -> int | None:
    opening = command[index]
    if opening not in {"{", "("}:
        return None
    if opening == "{" and index > 0 and command[index - 1] in {"$", "@"}:
        return None

    closing = "}" if opening == "{" else ")"
    group_end = _find_powershell_group_end(command, index, opening, closing)
    if group_end is None:
        return None
    buffer.extend(command[index : group_end + 1])
    nested_expressions.append(command[index + 1 : group_end])
    return group_end + 1


def _split_windows_command_parts(command: str) -> list[str]:
    parts: list[str] = []
    nested_expressions: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            buffer.append(char)
            escaped = False
            index += 1
            continue

        if char == "`" or (
            char == "^" and command[index + 1 : index + 2] in {"&", "|", ";"}
        ):
            buffer.append(char)
            escaped = True
            index += 1
            continue

        if quote != "'" and command[index : index + 2] == "$(":
            subexpression_end = _find_powershell_subexpression_end(command, index)
            if subexpression_end is not None:
                buffer.extend(command[index : subexpression_end + 1])
                nested_expressions.append(command[index + 2 : subexpression_end])
                index = subexpression_end + 1
                continue

        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            buffer.append(char)
            index += 1
            continue

        if quote is None:
            if (
                next_index := _append_powershell_group(
                    command, index, buffer, nested_expressions
                )
            ) is not None:
                index = next_index
                continue
            pair = command[index : index + 2]
            if pair in {"&&", "||"}:
                _flush_windows_command_part(parts, buffer)
                index += 2
                continue
            if char in {"&", "|", ";", "\n", "\r"}:
                _flush_windows_command_part(parts, buffer)
                index += 1
                continue

        buffer.append(char)
        index += 1

    _flush_windows_command_part(parts, buffer)
    for nested_expression in nested_expressions:
        parts.extend(_split_windows_command_parts(nested_expression))
    return parts


def _split_windows_command_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True)
        lexer.whitespace_split = True
        lexer.escape = ""
        return list(lexer)
    except ValueError:
        return command.split()


def _strip_windows_executable_suffix(value: str) -> str:
    lowered = value.lower()
    for suffix in _WINDOWS_EXECUTABLE_SUFFIXES:
        if lowered.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _windows_basename(value: str) -> str:
    return PureWindowsPath(value.strip("\"'")).name


def _windows_command_name(value: str) -> str:
    return _strip_windows_executable_suffix(_windows_basename(value)).lower()


def _windows_invoked_command(tokens: list[str]) -> tuple[str, list[str]]:
    if tokens[0] == "&" and len(tokens) > 1:
        return tokens[1].strip("\"'"), tokens[2:]
    return tokens[0].strip("\"'"), tokens[1:]


def _windows_powershell_alias_target(executable: str) -> str | None:
    value = executable.strip("\"'")
    # PowerShell aliases apply only to bare, unsuffixed command names. Expanding
    # C:\tools\rm.exe would let a remove-item allowlist approve another program.
    if value != _windows_basename(value):
        return None
    if value != _strip_windows_executable_suffix(value):
        return None
    return _WINDOWS_POWERSHELL_COMMAND_ALIASES.get(value.lower())


def _windows_command_match_forms(
    command: str, *, include_path_basename: bool = True
) -> list[str]:
    tokens = _split_windows_command_tokens(command)
    if not tokens:
        return []

    executable, rest = _windows_invoked_command(tokens)
    executable_basename = _windows_basename(executable)
    candidates = [executable]
    if include_path_basename or executable == executable_basename:
        candidates.extend([
            executable_basename,
            _strip_windows_executable_suffix(executable_basename),
        ])
    if alias_target := _windows_powershell_alias_target(executable):
        candidates.append(alias_target)

    forms = [" ".join(tokens).strip().lower()] if tokens[0] == "&" else []
    seen: set[str] = set()
    seen.update(forms)
    for candidate in candidates:
        if not candidate:
            continue
        form = " ".join([candidate, *rest]).strip().lower()
        if form in seen:
            continue
        seen.add(form)
        forms.append(form)
    return forms


def _windows_policy_pattern_forms(pattern: str) -> list[str]:
    normalized = pattern.lower()
    tokens = _split_windows_command_tokens(pattern)
    if not tokens:
        return [normalized]

    executable, rest = _windows_invoked_command(tokens)
    alias_target = _windows_powershell_alias_target(executable)
    if alias_target is None:
        return [normalized]

    canonical = " ".join([alias_target, *rest]).strip().lower()
    return [normalized] if canonical == normalized else [normalized, canonical]


def _windows_matches_policy_pattern(
    command: str, pattern: str, *, include_path_basename: bool = True
) -> bool:
    return any(
        _matches_pattern(command_form, pattern_form)
        for command_form in _windows_command_match_forms(
            command, include_path_basename=include_path_basename
        )
        for pattern_form in _windows_policy_pattern_forms(pattern)
    )


def _windows_looks_like_option(token: str) -> bool:
    if token.startswith("-"):
        return True
    if not token.startswith("/") or token.startswith("//"):
        return False
    body = token[1:]
    return bool(body) and "/" not in body and "\\" not in body and ":" not in body


def _windows_looks_like_path(token: str) -> bool:
    value = token.strip("\"'")
    return (
        value.startswith(("~", ".", "\\\\"))
        or (len(value) >= _WINDOWS_DRIVE_PREFIX_LENGTH and value[1] == ":")
        or "/" in value
        or "\\" in value
    )


_WINDOWS_POWERSHELL_VARIABLE = re.compile(
    r"\$(?:\{(?P<braced_name>[^}]+)\}"
    r"|(?:(?P<scope>[A-Za-z_][\w]*):)?(?P<name>[A-Za-z_][\w]*))",
    re.IGNORECASE,
)
_WINDOWS_POWERSHELL_PROVIDER_PATH = re.compile(
    r"^(?P<provider>[A-Za-z][\w.-]*)::(?P<path>.*)$"
)


def _windows_environment_value(environment: dict[str, str], name: str) -> str | None:
    name = name.casefold()
    return next(
        (value for key, value in environment.items() if key.casefold() == name), None
    )


def _windows_home_value(environment: dict[str, str]) -> str | None:
    if user_profile := _windows_environment_value(environment, "USERPROFILE"):
        return user_profile
    home_drive = _windows_environment_value(environment, "HOMEDRIVE")
    home_path = _windows_environment_value(environment, "HOMEPATH")
    if home_drive is None or home_path is None:
        return None
    return f"{home_drive}{home_path}"


def _expand_windows_powershell_path(
    token: str, *, command_cwd: Path, environment: dict[str, str]
) -> tuple[str, bool]:
    value = token.strip("\"'")
    unresolved = False

    def replace_variable(match: re.Match[str]) -> str:
        nonlocal unresolved
        if braced_name := match.group("braced_name"):
            possible_scope, separator, name = braced_name.partition(":")
            scope = possible_scope if separator else None
        else:
            name = match.group("name")
            scope = match.group("scope")
        if scope is None and name.casefold() == "pwd":
            return str(command_cwd)
        if scope is None and name.casefold() == "home":
            resolved = _windows_home_value(environment)
        elif scope is not None and scope.casefold() == "env":
            resolved = _windows_environment_value(environment, name)
        else:
            resolved = None
        if resolved is not None:
            return resolved
        unresolved = True
        return match.group(0)

    value = _WINDOWS_POWERSHELL_VARIABLE.sub(replace_variable, value)
    unresolved = unresolved or "$" in value or "@(" in value
    if value == "~" or value.startswith(("~/", "~\\")):
        value = f"{Path.home()}{value[1:]}"
    return value, unresolved


def _windows_attached_parameter_value(token: str) -> str | None:
    if not token.startswith("-"):
        return None
    match = re.match(r"^-[^:=\s]+[:=](.+)$", token)
    return match.group(1) if match else None


def _read_windows_redirection_target(
    command: str, index: int
) -> tuple[str | None, int]:
    if command[index : index + 1] == ">":
        index += 1
    while command[index : index + 1].isspace():
        index += 1
    if command[index : index + 1] == "&":
        return None, index + 1

    target: list[str] = []
    quote: str | None = None
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            target.append(char)
            escaped = False
            index += 1
            continue
        if char == "`":
            escaped = True
            index += 1
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            else:
                target.append(char)
            index += 1
            continue
        if quote is None and (char.isspace() or char in {"&", "|", ";", ">"}):
            break
        target.append(char)
        index += 1
    return "".join(target).strip() or None, index


def _windows_redirection_targets(command: str) -> list[str]:
    targets: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "`":
            escaped = True
            index += 1
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            index += 1
            continue
        if quote is not None or char != ">":
            index += 1
            continue

        target, index = _read_windows_redirection_target(command, index + 1)
        if target is not None:
            targets.append(target)

    return targets


def _windows_path_parent(
    token: str,
    *,
    command_cwd: Path,
    workspace: Workspace,
    scratchpad_dir: Path | None,
    environment: dict[str, str],
) -> tuple[str | None, bool]:
    value, unresolved = _expand_windows_powershell_path(
        token, command_cwd=command_cwd, environment=environment
    )
    unsupported_provider = False
    if provider_path := _WINDOWS_POWERSHELL_PROVIDER_PATH.match(value):
        unsupported_provider = (
            provider_path.group("provider").casefold() != "filesystem"
        )
        if not unsupported_provider:
            value = provider_path.group("path")
    if unresolved or unsupported_provider:
        return None, True
    if not _windows_looks_like_path(value):
        return None, False

    windows_path = PureWindowsPath(value)
    host_path = Path(value)
    if windows_path.is_absolute() and not host_path.is_absolute():
        return str(windows_path.parent), False
    if windows_path.drive and not windows_path.root:
        return None, True

    resolved = Path(value.replace("\\", os.sep)).expanduser()
    if not resolved.is_absolute():
        resolved = command_cwd / resolved
    resolved = resolved.resolve()

    if is_path_within_workdir(str(resolved), workspace=workspace) or is_scratchpad_path(
        str(resolved), scratchpad_dir=scratchpad_dir
    ):
        return None, False
    return str(resolved) if resolved.is_dir() else str(resolved.parent), False


def _analyze_windows_paths(
    command_parts: list[str],
    *,
    command_cwd: Path,
    workspace: Workspace,
    scratchpad_dir: Path | None,
    environment: dict[str, str],
) -> tuple[set[str], set[str]]:
    dirs: set[str] = set()
    dynamic_paths: set[str] = set()
    if not is_path_within_workdir(
        str(command_cwd), workspace=workspace
    ) and not is_scratchpad_path(str(command_cwd), scratchpad_dir=scratchpad_dir):
        dirs.add(str(command_cwd))

    def collect(token: str) -> None:
        parent, dynamic = _windows_path_parent(
            token,
            command_cwd=command_cwd,
            workspace=workspace,
            scratchpad_dir=scratchpad_dir,
            environment=environment,
        )
        if parent is not None:
            dirs.add(parent)
        if dynamic:
            dynamic_paths.add(token)

    for part in command_parts:
        tokens = _split_windows_command_tokens(part)
        if not tokens:
            continue
        executable, arguments = _windows_invoked_command(tokens)
        if executable != _windows_basename(executable):
            collect(executable)
        for target in _windows_redirection_targets(part):
            collect(target)

        command = _windows_command_name(executable)
        if not command or command in _WINDOWS_NON_PATH_COMMANDS:
            continue

        for token in arguments:
            if _windows_looks_like_option(token):
                if value := _windows_attached_parameter_value(token):
                    collect(value)
                    continue
                if token.startswith("-") or command in _WINDOWS_SLASH_OPTION_COMMANDS:
                    continue
            collect(token)
    return dirs, dynamic_paths


def _get_windows_env_overrides(overrides: dict[str, str] | None) -> dict[str, str]:
    env = {
        "CI": "true",
        "NONINTERACTIVE": "1",
        "NO_TTY": "1",
        "GIT_PAGER": "more",
        "PAGER": "more",
    }
    if overrides:
        env.update(overrides)
    return env


def _get_windows_base_env(overrides: dict[str, str] | None) -> dict[str, str]:
    return {**os.environ, **_get_windows_env_overrides(overrides)}


class WindowsShellToolConfig(ExperimentalBashToolConfig):
    pass


class WindowsShellArgs(BaseModel):
    command: str = Field(description="PowerShell command to run.")
    timeout: int | None = Field(
        default=None, description="Backward-compatible timeout override in seconds."
    )
    timeout_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Foreground wait time before the command is killed.",
    )
    cwd: ToolPath | None = Field(
        default=None, description="Working directory override."
    )
    env: dict[str, str] | None = Field(
        default=None, description="Environment variable overrides."
    )
    shell: str | None = Field(default=None, description="Shell executable override.")


class WindowsShellPermissionMixin[ConfigT: BashToolConfig](
    _BashPermissionMixin[ConfigT]
):
    def _find_denylist_match(self, command: str) -> str | None:
        return next(
            (
                pattern
                for pattern in self.config.denylist
                if _windows_matches_policy_pattern(command, pattern)
            ),
            None,
        )

    def _is_standalone_denylisted(self, command: str) -> bool:
        tokens = _split_windows_command_tokens(command)
        if not tokens:
            return False
        _executable, arguments = _windows_invoked_command(tokens)
        if arguments:
            return False
        return any(
            command_form == pattern_form
            for command_form in _windows_command_match_forms(command)
            for pattern in self.config.denylist_standalone
            for pattern_form in _windows_policy_pattern_forms(pattern)
        )

    def _is_allowlisted(self, command: str) -> bool:
        return any(
            _windows_matches_policy_pattern(
                command, pattern, include_path_basename=False
            )
            for pattern in self.config.allowlist
        )

    def _is_sensitive(self, command: str) -> bool:
        return any(
            _windows_matches_policy_pattern(command, pattern)
            for pattern in self.config.sensitive_patterns
        )

    @staticmethod
    def _has_find_execution_predicate(command: str) -> bool:
        _ = command
        return False

    def _build_windows_context_permissions(
        self, shell: str | None, env: dict[str, str] | None
    ) -> list[RequiredPermission]:
        required: list[RequiredPermission] = []
        if shell:
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=f"shell override: {shell}",
                    session_pattern=f"shell override: {shell}",
                    label=f"custom shell ({shell})",
                )
            )
        if env:
            names = ", ".join(sorted(env))
            required.append(
                self._build_command_required_permission(
                    invocation_pattern=f"env override: {names}",
                    session_pattern="env override *",
                    label=f"custom environment ({names})",
                )
            )
        return required

    def _resolve_windows_permission(
        self,
        *,
        command: str,
        cwd: str | None,
        shell: str | None,
        env: dict[str, str] | None,
    ) -> PermissionContext | None:
        command_parts = _split_windows_command_parts(command)
        if not command_parts:
            return None

        guardrail_permission = self._resolve_guardrail_permission(command_parts)
        if (
            guardrail_permission
            and guardrail_permission.permission == ToolPermission.NEVER
        ):
            return guardrail_permission

        command_cwd = resolve_tool_path(cwd, self.cwd)
        outside_dirs, dynamic_paths = _analyze_windows_paths(
            command_parts,
            command_cwd=command_cwd,
            workspace=self.workspace,
            scratchpad_dir=self.scratchpad_dir,
            environment={**os.environ, **(env or {})},
        )
        context_required = self._build_windows_context_permissions(shell, env)
        if (
            self._is_unconditionally_allowed(
                command_parts, outside_dirs | dynamic_paths, context_required
            )
            and not guardrail_permission
        ):
            return PermissionContext(permission=ToolPermission.ALWAYS)

        required = self._build_required_permissions(
            command_parts, outside_dirs, context_required
        )
        required.extend(
            self._build_command_required_permission(
                invocation_pattern=f"dynamic path: {path}",
                session_pattern=f"dynamic path: {path}",
                label=f"dynamic PowerShell path ({path})",
            )
            for path in sorted(dynamic_paths)
        )
        if guardrail_permission:
            required.extend(guardrail_permission.required_permissions)
        if not required:
            return None
        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )


class WindowsShell(
    WindowsShellPermissionMixin[WindowsShellToolConfig],
    BaseTool[
        WindowsShellArgs, CapturedShellResult, WindowsShellToolConfig, BaseToolState
    ],
    ToolUIData[WindowsShellArgs, CapturedShellResult],
):
    effect_kind = ToolEffectKind.SHELL
    description: ClassVar[str] = "Run a PowerShell command."
    shell_rollout: ClassVar[str | None] = "managed"
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "powershell_fallback.md"
    )

    @classmethod
    def get_name(cls) -> str:
        return "powershell"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return is_windows() and _powershell_treatment_available()

    @classmethod
    def format_call_display(cls, args: WindowsShellArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"powershell: {args.command}",
            verb="Running",
            message=args.command,
            settled_verb="Ran",
            settled_message=args.command,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, CapturedShellResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(success=True, verb="Ran", message=event.result.command)

    @classmethod
    def get_status_text(cls) -> str:
        return "Running Windows command"

    def resolve_permission(self, args: WindowsShellArgs) -> PermissionContext | None:
        return self._resolve_windows_permission(
            command=args.command, cwd=args.cwd, shell=args.shell, env=args.env
        )

    async def run(
        self, args: WindowsShellArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | CapturedShellResult, None]:
        requested_timeout = (
            float(args.timeout) if args.timeout is not None else args.timeout_seconds
        )
        timeout = self._resolve_timeout(requested_timeout)
        max_bytes = self.config.max_output_bytes
        proc: asyncio.subprocess.Process | None = None
        try:
            cwd = resolve_tool_path(args.cwd, self.cwd)
            shell = resolve_powershell_shell(args.shell, self.config.shell)
            argv = build_windows_shell_argv(shell, args.command)
            if (
                ctx is not None
                and ctx.tool_io is not None
                and ctx.tool_io.supports_terminal
                and ctx.session_id is not None
            ):
                try:
                    result = await ctx.tool_io.run_shell(
                        ShellCommandRequest(
                            session_id=ctx.session_id,
                            tool_call_id=ctx.tool_call_id,
                            command=argv[0],
                            args=argv[1:],
                            env=_get_windows_env_overrides(args.env),
                            cwd=cwd,
                            timeout=timeout,
                            max_output_bytes=max_bytes,
                        )
                    )
                except TimeoutError:
                    raise ToolError(
                        f"Command timed out after {timeout:g}s: {args.command!r}"
                    ) from None
                yield completed_shell_result(
                    command=args.command,
                    shell=shell,
                    stdout=result.stdout[:max_bytes],
                    stderr=result.stderr[:max_bytes],
                    exit_code=result.returncode,
                )
                return

            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
                env=_get_windows_base_env(args.env),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                await kill_async_subprocess(proc)
                raise ToolError(
                    f"Command timed out after {timeout:g}s: {args.command!r}"
                )

            stdout = _decode_limited(stdout_bytes, max_bytes)
            stderr = _decode_limited(stderr_bytes, max_bytes)
            yield completed_shell_result(
                command=args.command,
                shell=shell,
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode or 0,
            )
        except (ToolError, asyncio.CancelledError):
            raise
        except (ManagedShellError, ManagedShellBackendError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Error running command {args.command!r}: {exc}") from exc
        finally:
            if proc is not None:
                await kill_async_subprocess(proc)

    def _resolve_timeout(self, requested: float | None) -> float:
        timeout = self.config.default_timeout if requested is None else requested
        return min(timeout, self.config.max_timeout_seconds)


class ExperimentalWindowsShell(
    WindowsShellPermissionMixin[ExperimentalBashToolConfig], ExperimentalBash
):
    description: ClassVar[str] = "Run a PowerShell command in a managed PTY session."
    prompt_path = (
        VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "managed_powershell.md"
    )
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "powershell"
    session_prefix: ClassVar[str] = WINDOWS_SESSION_PREFIX

    @classmethod
    def get_name(cls) -> str:
        return "powershell"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _windows_managed_shell_enabled(config)

    @classmethod
    def format_call_display(cls, args: ExperimentalBashArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"powershell: {args.command}",
            verb="Running",
            message=args.command,
            settled_verb="Ran",
            settled_message=args.command,
        )

    def resolve_permission(
        self, args: ExperimentalBashArgs
    ) -> PermissionContext | None:
        return self._resolve_windows_permission(
            command=args.command, cwd=args.cwd, shell=args.shell, env=args.env
        )


class WindowsShellOutput(BashOutput):
    description: ClassVar[str] = (
        "Poll output from a running or completed PowerShell session."
    )
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "powershell"
    session_prefix: ClassVar[str] = WINDOWS_SESSION_PREFIX
    session_label: ClassVar[str] = "powershell"

    @classmethod
    def get_name(cls) -> str:
        return "powershell_output"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _windows_managed_shell_enabled(config)


class WindowsShellStdin(BashStdin):
    description: ClassVar[str] = "Send input to an interactive PowerShell session."
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "powershell"
    session_prefix: ClassVar[str] = WINDOWS_SESSION_PREFIX
    session_label: ClassVar[str] = "powershell"

    @classmethod
    def get_name(cls) -> str:
        return "powershell_stdin"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _windows_managed_shell_enabled(config)

    def _build_payload(self, args: BashStdinArgs) -> bytes:
        if args.text is None:
            return super()._build_payload(args)
        translated_args = args.model_copy(
            update={"text": args.text.replace("\r\n", "\n").replace("\n", "\r")}
        )
        return super()._build_payload(translated_args)


class WindowsShellSessions(BashSessions):
    description: ClassVar[str] = (
        "List, inspect, kill, or reset managed PowerShell sessions."
    )
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "powershell"
    session_prefix: ClassVar[str] = WINDOWS_SESSION_PREFIX
    session_label: ClassVar[str] = "powershell"

    @classmethod
    def get_name(cls) -> str:
        return "powershell_sessions"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _windows_managed_shell_enabled(config)


class WindowsShellLogFile(BashLogFile):
    description: ClassVar[str] = "Read or annotate managed PowerShell output files."
    shell_rollout: ClassVar[str | None] = "managed"
    shell_family: ClassVar[str] = "powershell"
    session_prefix: ClassVar[str] = WINDOWS_SESSION_PREFIX
    session_label: ClassVar[str] = "powershell"

    @classmethod
    def get_name(cls) -> str:
        return "powershell_log_file"

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return _windows_managed_shell_enabled(config)


def _decode_limited(raw: bytes | None, max_bytes: int) -> str:
    if not raw:
        return ""
    return decode_console_safe(raw[:max_bytes])
