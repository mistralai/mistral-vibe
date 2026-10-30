# Twin: mistralai.vibe.harness.runtime.completion.profile.known_models. Keep both copies identical below this line.
"""Layer 3: facts shipped in the code for models we know, used with no network.

Rows are tried in order and the first match wins, so a specific pattern comes before a
general one. A pattern matches the lowercased model id after its last `/`. Rows never
carry a threshold; the threshold comes from the user or the rule.
"""

from collections.abc import Sequence
from fnmatch import fnmatchcase

from .types import (
    NO_REASONING,
    TEXT_AND_IMAGE,
    TEXT_ONLY,
    KnownModel,
    ReasoningControl,
    ReasoningLevel,
    ReasoningSupport,
)

type KnownModelRow = tuple[tuple[str, ...], KnownModel]

_ALL_LEVELS: tuple[ReasoningLevel, ...] = ("low", "medium", "high", "xhigh", "max")
_CLAUDE_WINDOW = 1_000_000
_CLAUDE_MAX_OUTPUT = 128_000


_SNAPSHOT_DATE = "20" + "[0-9]" * 6


def _claude(*names: str) -> tuple[str, ...]:
    """Match a Claude id, its dated snapshots and its Vertex form."""
    return tuple(
        form
        for name in names
        for form in (name, f"{name}-{_SNAPSHOT_DATE}", f"{name}@{_SNAPSHOT_DATE}")
    )


def _claude_model(
    levels: tuple[ReasoningLevel, ...] = _ALL_LEVELS, *, can_turn_off: bool
) -> KnownModel:
    return KnownModel(
        context_window=_CLAUDE_WINDOW,
        max_output_tokens=_CLAUDE_MAX_OUTPUT,
        input_modalities=TEXT_AND_IMAGE,
        reasoning=ReasoningSupport(levels, can_turn_off=can_turn_off),
    )


_OPENAI_TO_XHIGH: tuple[ReasoningLevel, ...] = ("low", "medium", "high", "xhigh")
_OPENAI_PRO = ReasoningSupport(("medium", "high", "xhigh"), can_turn_off=False)
_OPENAI_SNAPSHOT_DATE = "20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def _openai(*names: str) -> tuple[str, ...]:
    """Match an OpenAI id and its dated snapshots."""
    return tuple(
        form for name in names for form in (name, f"{name}-{_OPENAI_SNAPSHOT_DATE}")
    )


def _openai_model(
    context_window: int,
    reasoning: ReasoningSupport | None,
    max_output_tokens: int = 128_000,
) -> KnownModel:
    return KnownModel(
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        input_modalities=TEXT_AND_IMAGE,
        reasoning=reasoning,
    )


_MISTRAL_MEDIUM_3_5 = KnownModel(
    context_window=262_144,
    input_modalities=TEXT_AND_IMAGE,
    reasoning=ReasoningSupport(("high",), can_turn_off=True),
)
# The Mistral API card lists mistral-large-latest as an alias of mistral-large-2512,
# a model without reasoning that rejects every reasoning effort.
_MISTRAL_LARGE_2512 = KnownModel(
    context_window=262_144, input_modalities=TEXT_AND_IMAGE, reasoning=NO_REASONING
)
# The Mistral API takes only the efforts low, high and max for Mistral Large 4,
# and rejects none, so its reasoning cannot be turned off.
_MISTRAL_LARGE_4 = KnownModel(
    context_window=1_048_576,
    input_modalities=TEXT_AND_IMAGE,
    reasoning=ReasoningSupport(("low", "high", "max"), can_turn_off=False),
)

# The Mistral API lists this window and no image input for GLM 5.3, and takes only
# the efforts low, high and max.
_GLM_5_3 = KnownModel(
    context_window=1_048_576,
    input_modalities=TEXT_ONLY,
    reasoning=ReasoningSupport(("low", "high", "max"), can_turn_off=False),
)

KNOWN_MODELS: Sequence[KnownModelRow] = (
    (
        (
            "mistral-medium-3.5",
            "mistral-medium-3-5",
            "mistral-medium-2604",
            "mistral-medium-latest",
            "magistral-medium-latest",
            "mistral-vibe-cli-latest",
            "mistral-vibe-cli-with-tools",
        ),
        _MISTRAL_MEDIUM_3_5,
    ),
    (("mistral-large-4", "mistral-large-4-0"), _MISTRAL_LARGE_4),
    (("mistral-large-2512", "mistral-large-latest"), _MISTRAL_LARGE_2512),
    (("zai-glm-5-3", "zai-glm-5", "zai-glm-latest"), _GLM_5_3),
    (_claude("claude-haiku-5-5"), _claude_model(can_turn_off=True)),
    (
        _claude("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"),
        _claude_model(can_turn_off=False),
    ),
    (_claude("claude-fable-5"), _claude_model(can_turn_off=False)),
    (
        _claude(
            "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7"
        ),
        _claude_model(can_turn_off=True),
    ),
    (
        _claude("claude-opus-4-6", "claude-sonnet-4-6"),
        _claude_model(("low", "medium", "high", "max"), can_turn_off=True),
    ),
    (
        _claude("claude-haiku-4-5"),
        KnownModel(
            context_window=200_000,
            max_output_tokens=64_000,
            input_modalities=TEXT_AND_IMAGE,
            reasoning=ReasoningSupport(
                ("low", "medium", "high"),
                can_turn_off=True,
                control=ReasoningControl.BUDGET_TOKENS,
            ),
        ),
    ),
    # OpenAI's model pages list these windows, max outputs and reasoning efforts.
    # A model whose page lists no efforts has unknown reasoning. OpenAI's minimal
    # effort has no standard level; low is the lowest one sent.
    (
        _openai("gpt-6-astra", "gpt-6.1-sol"),
        _openai_model(1_050_000, ReasoningSupport(_ALL_LEVELS, can_turn_off=False)),
    ),
    (
        _openai(
            "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"
        ),
        _openai_model(1_050_000, ReasoningSupport(_ALL_LEVELS, can_turn_off=True)),
    ),
    (
        _openai("gpt-5.5", "gpt-5.4"),
        _openai_model(1_050_000, ReasoningSupport(_OPENAI_TO_XHIGH, can_turn_off=True)),
    ),
    (_openai("gpt-5.5-pro", "gpt-5.4-pro"), _openai_model(1_050_000, _OPENAI_PRO)),
    (
        _openai("gpt-5-pro"),
        _openai_model(
            400_000,
            ReasoningSupport(("high",), can_turn_off=False),
            max_output_tokens=272_000,
        ),
    ),
    (
        _openai("gpt-5.2", "gpt-5.4-mini"),
        _openai_model(400_000, ReasoningSupport(_OPENAI_TO_XHIGH, can_turn_off=True)),
    ),
    (_openai("gpt-5.2-pro"), _openai_model(400_000, _OPENAI_PRO)),
    (
        _openai("gpt-5"),
        _openai_model(
            400_000, ReasoningSupport(("low", "medium", "high"), can_turn_off=False)
        ),
    ),
    (
        _openai("gpt-5.6-cyber", "gpt-5-mini", "gpt-5-nano"),
        _openai_model(400_000, None),
    ),
)


# Anthropic checks on these models that a replayed signed thinking block still
# follows the same history, and accepts `thinking.block_binding` to drop a stale
# block instead of failing. Other models reject the field.
_BINDS_THINKING_BLOCKS = _claude(
    "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"
)


def binds_thinking_blocks(model: str) -> bool:
    """Whether the model takes Anthropic's `thinking.block_binding` field."""
    name = model.rsplit("/", 1)[-1].lower()
    return any(fnmatchcase(name, pattern) for pattern in _BINDS_THINKING_BLOCKS)


def find_known_model(
    model: str, rows: Sequence[KnownModelRow] = KNOWN_MODELS
) -> KnownModel | None:
    """Return the first row matching the model id, or `None`."""
    name = model.rsplit("/", 1)[-1].lower()
    for patterns, known in rows:
        if any(fnmatchcase(name, pattern) for pattern in patterns):
            return known
    return None
