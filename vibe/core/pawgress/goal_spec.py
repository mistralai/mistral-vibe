"""Parse `/pawgress` input and, when only a natural-language goal is given,
generate a machine-checkable acceptance spec from it.

Two independent concerns live here, both pure and unit-testable:

- `parse_pawgress_args` — split the command line into (description, verify,
  repeat, constraints, flags_present). `flags_present` is what lets the caller
  choose between the explicit arg path (unchanged) and the generated path.
- `collect_repo_context` / `build_generation_messages` / `parse_generation_response`
  — the pieces of the LLM round-trip that turns a goal into a verify command.
  The actual model call happens in the caller; everything here is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
from typing import NamedTuple

from vibe.core.types import LLMMessage, Role


class ParsedPawgressArgs(NamedTuple):
    description: str
    verify: str | None
    repeat: int
    constraints: list[str]
    flags_present: bool


def parse_pawgress_args(cmd_args: str) -> ParsedPawgressArgs:
    tokens = shlex.split(cmd_args)
    description_parts: list[str] = []
    verify: str | None = None
    repeat = 1
    constraints: list[str] = []
    seen_flag = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        match token:
            case "--verify":
                seen_flag = True
                if i + 1 < len(tokens):
                    i += 1
                    verify = tokens[i]
            case "--repeat":
                seen_flag = True
                if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                    i += 1
                    repeat = max(int(tokens[i]), 1)
            case "--constraint":
                seen_flag = True
                if i + 1 < len(tokens):
                    i += 1
                    constraints.append(tokens[i])
            case _:
                if not seen_flag:
                    description_parts.append(token)
        i += 1
    return ParsedPawgressArgs(
        " ".join(description_parts), verify, repeat, constraints, seen_flag
    )


@dataclass
class GeneratedGoalSpec:
    verify_command: str | None
    repeat: int = 1
    constraints: list[str] = field(default_factory=list)


_TEST_RUNNER_HINTS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "Python project — run tests with `uv run pytest` or `pytest`."),
    ("pytest.ini", "Python project — run tests with `pytest`."),
    ("package.json", "Node project — run tests with `npm test` (check package.json)."),
    ("Cargo.toml", "Rust project — run tests with `cargo test`."),
    ("go.mod", "Go project — run tests with `go test ./...`."),
    ("Makefile", "Has a Makefile — `make test` / `make check` may exist."),
)


def collect_repo_context(cwd: Path) -> str:
    """A small, bounded fingerprint of the repo to ground the generated command."""
    lines: list[str] = []
    for filename, hint in _TEST_RUNNER_HINTS:
        if (cwd / filename).exists():
            lines.append(hint)

    test_paths: list[str] = []
    for name in ("tests", "test"):
        if (cwd / name).is_dir():
            test_paths.append(f"{name}/")
    for path in sorted(cwd.glob("*test*.py"))[:5]:
        test_paths.append(path.name)
    if test_paths:
        lines.append("Test locations: " + ", ".join(test_paths))

    if not lines:
        lines.append("No obvious test runner detected.")
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You turn a natural-language coding goal into a single machine-checkable "
    "acceptance test for the Pawgress goal runtime. The acceptance test is a "
    "shell command that exits 0 if and only if the goal is met. Prefer the "
    "project's existing test runner and target the narrowest relevant tests. "
    "The command must be runnable from the repository root."
)


def build_generation_messages(description: str, repo_context: str) -> list[LLMMessage]:
    user = (
        f"Goal: {description}\n\n"
        f"Repository context:\n{repo_context}\n\n"
        "Return ONLY a JSON object, no prose, with this shape:\n"
        '{"verify_command": "<shell command, or null if none can be determined>", '
        '"repeat": <integer, times to re-run to guard against flakiness, default 1>, '
        '"constraints": ["<short natural-language checklist item>", ...]}\n\n'
        "Rules: verify_command runs from the repo root and must exit 0 only when "
        "the goal is truly met. Use the project's existing test runner. Set "
        "verify_command to null if you cannot determine a meaningful check. "
        "constraints may be an empty list."
    )
    return [
        LLMMessage(role=Role.system, content=_SYSTEM_PROMPT),
        LLMMessage(role=Role.user, content=user),
    ]


def _extract_json_object(text: str) -> dict | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : idx + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def parse_generation_response(text: str) -> GeneratedGoalSpec:
    obj = _extract_json_object(text)
    if obj is None:
        return GeneratedGoalSpec(verify_command=None)

    verify_raw = obj.get("verify_command")
    verify_command = (
        verify_raw.strip()
        if isinstance(verify_raw, str) and verify_raw.strip()
        else None
    )

    repeat_raw = obj.get("repeat", 1)
    repeat = (
        max(1, int(repeat_raw))
        if isinstance(repeat_raw, int) and not isinstance(repeat_raw, bool)
        else 1
    )

    constraints_raw = obj.get("constraints", [])
    constraints = (
        [c.strip() for c in constraints_raw if isinstance(c, str) and c.strip()]
        if isinstance(constraints_raw, list)
        else []
    )

    return GeneratedGoalSpec(
        verify_command=verify_command, repeat=repeat, constraints=constraints
    )
