from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vibe.cli.commands import CommandRegistry


@dataclass(frozen=True, slots=True)
class Teleport:
    target: str


@dataclass(frozen=True, slots=True)
class SlashCommand:
    pass


@dataclass(frozen=True, slots=True)
class Skill:
    command: str
    name: str


@dataclass(frozen=True, slots=True)
class Bash:
    command: str


@dataclass(frozen=True, slots=True)
class EmptyBash:
    pass


@dataclass(frozen=True, slots=True)
class Prompt:
    text: str


ClassifiedInput = Teleport | SlashCommand | Skill | Bash | EmptyBash | Prompt


def classify(
    value: str,
    *,
    commands: CommandRegistry,
    resolve_skill: Callable[[str], Skill | None],
) -> ClassifiedInput:
    if value.startswith("&") and commands.has_command("teleport"):
        return Teleport(target=value[1:])
    if commands.parse_command(value) is not None:
        return SlashCommand()
    if value.startswith("/"):
        if (skill := resolve_skill(value)) is not None:
            return skill
    if value.startswith("!"):
        cmd = value[1:]
        return EmptyBash() if not cmd else Bash(command=cmd)
    return Prompt(text=value)
