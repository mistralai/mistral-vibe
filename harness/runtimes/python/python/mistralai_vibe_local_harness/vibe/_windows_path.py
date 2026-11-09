from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib
import re
import sys
from typing import Any

_MACHINE_ENVIRONMENT_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
)
_USER_ENVIRONMENT_KEY = "Environment"
_VARIABLE_REFERENCE = re.compile(r"%([^%]+)%")


def with_registry_path(env: Mapping[str, str]) -> dict[str, str]:
    # Installers write PATH to the registry, which a running process never
    # re-reads: appending what it holds now lets a command find a tool
    # installed after this process started.
    if sys.platform != "win32":
        return dict(env)
    try:
        variables, path_values = _read_registry_environment()
    except (ImportError, OSError):
        return dict(env)
    return merge_registry_path(env, variables, path_values)


def merge_registry_path(
    env: Mapping[str, str],
    registry_variables: Mapping[str, str],
    registry_path_values: Sequence[str],
) -> dict[str, str]:
    path_key = next((key for key in env if key.casefold() == "path"), "PATH")
    entries = [entry for entry in env.get(path_key, "").split(";") if entry]
    seen = {_comparable(entry) for entry in entries}
    added = False
    for value in registry_path_values:
        expanded = _expand(value, env, registry_variables)
        for entry in expanded.split(";"):
            entry = entry.strip()
            if not entry or _comparable(entry) in seen:
                continue
            seen.add(_comparable(entry))
            entries.append(entry)
            added = True
    merged = dict(env)
    if added:
        merged[path_key] = ";".join(entries)
    return merged


def _expand(
    value: str, env: Mapping[str, str], registry_variables: Mapping[str, str]
) -> str:
    folded_env = {key.casefold(): item for key, item in env.items()}
    folded_registry = {key.casefold(): item for key, item in registry_variables.items()}
    resolved: dict[str, str] = {}

    def resolve(name: str, expanding: frozenset[str]) -> str | None:
        if name in resolved:
            return resolved[name]
        if name not in folded_registry or name in expanding:
            return folded_env.get(name)
        resolved[name] = substitute(folded_registry[name], expanding | {name})
        return resolved[name]

    def substitute(text: str, expanding: frozenset[str]) -> str:
        def replace(match: re.Match[str]) -> str:
            found = resolve(match.group(1).casefold(), expanding)
            return match.group(0) if found is None else found

        return _VARIABLE_REFERENCE.sub(replace, text)

    return substitute(value, frozenset())


def _comparable(entry: str) -> str:
    return entry.rstrip("\\/").casefold()


def _read_registry_environment() -> tuple[dict[str, str], list[str]]:
    winreg: Any = importlib.import_module("winreg")
    variables: dict[str, str] = {}
    path_values: list[str] = []
    # Machine first: a user variable overrides a machine one, and the user
    # PATH follows the machine PATH, as Windows builds a new process's PATH.
    for root, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENVIRONMENT_KEY),
        (winreg.HKEY_CURRENT_USER, _USER_ENVIRONMENT_KEY),
    ):
        try:
            key = winreg.OpenKey(root, subkey)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if kind not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}:
                    continue
                if not isinstance(value, str):
                    continue
                if name.casefold() == "path":
                    path_values.append(value)
                else:
                    variables[name] = value
    return variables, path_values
