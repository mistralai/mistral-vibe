from __future__ import annotations

from pathlib import Path

from vibe.core.pawgress.goal_spec import (
    GeneratedGoalSpec,
    collect_repo_context,
    parse_generation_response,
    parse_pawgress_args,
)

# --- Regression guard: existing arg-based behavior must never change ---


def test_parse_full_args_matches_legacy_behavior():
    parsed = parse_pawgress_args(
        '"Fix the bug" --verify "pytest -q" --repeat 3 --constraint "no new deps"'
    )
    assert parsed.description == "Fix the bug"
    assert parsed.verify == "pytest -q"
    assert parsed.repeat == 3
    assert parsed.constraints == ["no new deps"]
    assert parsed.flags_present is True


def test_parse_repeat_floor_and_multiple_constraints():
    parsed = parse_pawgress_args("goal here --repeat 0 --constraint a --constraint b")
    assert parsed.description == "goal here"
    assert parsed.repeat == 1  # max(int, 1)
    assert parsed.constraints == ["a", "b"]
    assert parsed.flags_present is True


def test_parse_goal_only_sets_flags_present_false():
    parsed = parse_pawgress_args("Fix the flaky cache test")
    assert parsed.description == "Fix the flaky cache test"
    assert parsed.verify is None
    assert parsed.repeat == 1
    assert parsed.constraints == []
    assert parsed.flags_present is False


def test_parse_empty_input():
    parsed = parse_pawgress_args("")
    assert parsed.description == ""
    assert parsed.flags_present is False


# --- Generation response parsing ---


def test_parse_plain_json():
    spec = parse_generation_response(
        '{"verify_command": "uv run pytest tests/x -q", "repeat": 5, '
        '"constraints": ["no public API change"]}'
    )
    assert spec.verify_command == "uv run pytest tests/x -q"
    assert spec.repeat == 5
    assert spec.constraints == ["no public API change"]


def test_parse_fenced_json_with_prose():
    text = (
        "Sure, here is the acceptance test:\n```json\n"
        '{"verify_command": "pytest -q", "repeat": 1, "constraints": []}\n'
        "```\nThat should work."
    )
    spec = parse_generation_response(text)
    assert spec.verify_command == "pytest -q"
    assert spec.repeat == 1
    assert spec.constraints == []


def test_parse_null_verify_command():
    spec = parse_generation_response('{"verify_command": null, "repeat": 2}')
    assert spec.verify_command is None
    assert spec.repeat == 2


def test_parse_missing_and_malformed_fields_use_defaults():
    spec = parse_generation_response('{"verify_command": "pytest"}')
    assert spec.verify_command == "pytest"
    assert spec.repeat == 1
    assert spec.constraints == []


def test_parse_garbage_returns_empty_spec():
    spec = parse_generation_response("I could not determine a command, sorry.")
    assert spec == GeneratedGoalSpec(verify_command=None, repeat=1, constraints=[])


def test_parse_empty_verify_string_becomes_none():
    spec = parse_generation_response('{"verify_command": "   "}')
    assert spec.verify_command is None


# --- Repo context detection ---


def test_collect_repo_context_detects_python_and_tests(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    context = collect_repo_context(tmp_path)
    assert "Python project" in context
    assert "tests/" in context


def test_collect_repo_context_empty_repo(tmp_path: Path):
    context = collect_repo_context(tmp_path)
    assert "No obvious test runner detected." in context
