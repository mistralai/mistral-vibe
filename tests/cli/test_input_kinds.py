from __future__ import annotations

import pytest

from vibe.cli.commands import CommandContext, CommandRegistry
from vibe.cli.textual_ui.widgets.chat_input.input_kinds import (
    Prompt,
    SlashCommand,
    classify,
)


@pytest.mark.parametrize("experimental_harness", [False, True])
@pytest.mark.parametrize(
    "value",
    [
        "/loop",
        "/loop list",
        "/loop cancel",
        "/loop cancel abc123",
        "/loop cancel all",
        "/loop 30s check the build",
        "/loop every weekday at 9am check the build",
        "  /LOOP\tlist  ",
        "/loop 30s check\nthe build",
    ],
)
def test_loop_classification_depends_on_active_harness(
    value: str, experimental_harness: bool
) -> None:
    result = classify(
        value,
        commands=CommandRegistry(
            context=CommandContext(experimental_harness=experimental_harness)
        ),
        resolve_skill=lambda _value: pytest.fail("/loop must not resolve a skill"),
    )

    if experimental_harness:
        assert result == Prompt(text=value)
    else:
        assert isinstance(result, SlashCommand)


def _classify(value: str) -> object:
    return classify(
        value, commands=CommandRegistry(), resolve_skill=lambda _value: None
    )


@pytest.mark.parametrize("alias", ["/exit", "exit", "quit", ":q", ":quit"])
def test_classify_treats_bare_exit_synonyms_as_slash_command(alias: str) -> None:
    assert isinstance(_classify(alias), SlashCommand)


@pytest.mark.parametrize("value", ["exit the function early", "quit your job"])
def test_classify_keeps_bare_synonym_with_trailing_text_as_prompt(value: str) -> None:
    result = _classify(value)
    assert isinstance(result, Prompt)
    assert result.text == value
