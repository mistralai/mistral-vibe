# Twin: mistralai.vibe.harness.runtime.completion.profile.reasoning. Keep both copies identical below this line.
"""Reasoning settings: what the user wrote, sent as written.

The standard settings (off, low, medium, high, xhigh, max) are always accepted. A
provider's own name goes through its format's alias table. The user's setting goes
out unchanged, and the provider says whether the model takes it. Only a request of
the Harness's own is fit to the model's known levels: the lowest setting is off
where the model can stop and its lowest level where it cannot, and a level the
model lacks rounds down to the nearest one it has. Each adapter then sends the
setting through its format's row of `REASONING_WIRE_VALUES`; nothing else maps
reasoning.
"""

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from .types import (
    REASONING_LEVELS,
    REASONING_LOWEST,
    REASONING_OFF,
    REASONING_SETTINGS,
    ProfileError,
    ReasoningControl,
    ReasoningLevel,
    ReasoningSetting,
    ReasoningSupport,
    RequestReasoning,
    WireFormat,
)

_logger = logging.getLogger(__name__)

_OPENAI_ALIASES: Mapping[str, ReasoningSetting] = {"none": "off", "minimal": "low"}

REASONING_ALIASES: Mapping[WireFormat, Mapping[str, ReasoningSetting]] = (
    MappingProxyType(
        {
            WireFormat.MISTRAL: MappingProxyType({"none": "off"}),
            WireFormat.OPENAI_CHAT: MappingProxyType(_OPENAI_ALIASES),
            WireFormat.OPENAI_RESPONSES: MappingProxyType(_OPENAI_ALIASES),
            WireFormat.ANTHROPIC: MappingProxyType({"disabled": "off"}),
            WireFormat.OLLAMA: MappingProxyType({"false": "off", "true": "medium"}),
        }
    )
)


def parse_reasoning(raw: str, wire: WireFormat) -> ReasoningSetting:
    """Read a standard setting or the format's own name for one."""
    name = raw.strip().lower()
    if name in REASONING_SETTINGS:
        return cast(ReasoningSetting, name)
    aliases = REASONING_ALIASES[wire]
    if name in aliases:
        return aliases[name]
    accepted = ", ".join([*REASONING_SETTINGS, *aliases])
    raise ProfileError(
        f"unknown reasoning setting {raw!r} for the {wire} format; accepted: {accepted}"
    )


def fit_reasoning(
    setting: ReasoningSetting, support: ReasoningSupport
) -> ReasoningSetting:
    """Fit a setting to the model's levels; refuse off on a model that always reasons.

    A model without reasoning takes only off, which then goes out as no field.
    """
    if not support.levels and setting != REASONING_OFF:
        raise ProfileError(
            "this model has no reasoning setting; leave reasoning unset or set it to off"
        )
    if setting == REASONING_OFF:
        if support.can_turn_off:
            return REASONING_OFF
        raise ProfileError(
            "reasoning cannot be turned off on this model; set one of its levels: "
            + ", ".join(support.levels)
        )
    rank = REASONING_LEVELS.index(setting)
    at_or_below: list[ReasoningLevel] = [
        level for level in support.levels if REASONING_LEVELS.index(level) <= rank
    ]
    return at_or_below[-1] if at_or_below else support.levels[0]


type ReasoningWireValue = str | bool

_EFFORT_WIRE: Mapping[ReasoningSetting, ReasoningWireValue] = MappingProxyType(
    {
        "off": "none",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "max",
    }
)

# The wire value of each standard setting, per format. Anthropic's off means no
# thinking block; Ollama's `think` takes a boolean or low, medium and high.
REASONING_WIRE_VALUES: Mapping[
    WireFormat, Mapping[ReasoningSetting, ReasoningWireValue]
] = MappingProxyType(
    {
        WireFormat.MISTRAL: _EFFORT_WIRE,
        WireFormat.OPENAI_CHAT: _EFFORT_WIRE,
        WireFormat.OPENAI_RESPONSES: _EFFORT_WIRE,
        WireFormat.ANTHROPIC: MappingProxyType(
            {
                "off": False,
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "xhigh",
                "max": "max",
            }
        ),
        WireFormat.OLLAMA: MappingProxyType(
            {
                "off": False,
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "high",
                "max": "high",
            }
        ),
    }
)

# Formats whose off value only a model known to reason accepts; a model without
# reasoning may reject the field, so off is left out for it.
_OFF_NEEDS_KNOWN_REASONING = frozenset(
    {WireFormat.MISTRAL, WireFormat.OPENAI_CHAT, WireFormat.OPENAI_RESPONSES}
)

# Thinking budgets of models that take a token budget instead of an effort.
THINKING_BUDGET_TOKENS: Mapping[ReasoningLevel, int] = MappingProxyType(
    {"low": 4_096, "medium": 16_384, "high": 32_000, "xhigh": 32_000, "max": 32_000}
)
# The smallest thinking budget the Anthropic API accepts.
MIN_THINKING_BUDGET_TOKENS = 1_024


def request_reasoning(
    wire: WireFormat,
    configured: ReasoningSetting | None,
    support: ReasoningSupport | None,
    requested: RequestReasoning | None = None,
) -> ReasoningSetting | None:
    """The setting one request sends, or `None` to leave the field out.

    - The route's setting, the user's choice, goes out as written: the provider
      says whether the model takes it.
    - A request's own setting (a compaction asking for the lowest or for off) is
      fit to the model's known levels.
    - Off goes out only where it means something: to a model known to reason, or,
      on effort formats, never to a model whose reasoning is unknown unless the
      route reasons. A model known to have no reasoning gets no field for off.
    """
    no_reasoning = support is not None and not support.levels
    if requested == REASONING_LOWEST:
        return None if no_reasoning else _lowest_reasoning(wire, configured, support)
    if requested is not None and support is not None:
        return None if no_reasoning else fit_reasoning(requested, support)
    setting = configured if requested is None else requested
    if setting is None:
        return None
    if setting == REASONING_OFF:
        if no_reasoning:
            return None
        if (
            support is None
            and wire in _OFF_NEEDS_KNOWN_REASONING
            and configured in (None, REASONING_OFF)
        ):
            return None
    return setting


def takes_reasoning_input(
    wire: WireFormat,
    configured: ReasoningSetting | None,
    support: ReasoningSupport | None,
    requested: RequestReasoning | None = None,
) -> bool:
    """Whether a request may carry the conversation's earlier reasoning back.

    A model known to reason takes it, and a model without reasoning rejects it. A
    model with unknown reasoning takes it only when the request asks it to reason:
    one that is sent no effort may reject reasoning input, as a model the Session
    moved to from a reasoning model does.
    """
    if support is not None:
        return bool(support.levels)
    return request_reasoning(wire, configured, None, requested) not in (
        None,
        REASONING_OFF,
    )


def _lowest_reasoning(
    wire: WireFormat,
    configured: ReasoningSetting | None,
    support: ReasoningSupport | None,
) -> ReasoningSetting | None:
    """The least reasoning a model takes: off where it can stop, else its lowest level.

    With unknown levels it is off, under the same rule as a request asking for off:
    a model known to always reason has a row with its levels.
    """
    if support is None:
        return request_reasoning(wire, configured, None, REASONING_OFF)
    if support.can_turn_off:
        return REASONING_OFF
    _logger.debug(
        "the model cannot turn reasoning off; the request uses its lowest level, %s",
        support.levels[0],
    )
    return support.levels[0]


def reasoning_wire_value(
    wire: WireFormat, setting: ReasoningSetting
) -> ReasoningWireValue:
    """The value a format's reasoning field carries for a standard setting."""
    return REASONING_WIRE_VALUES[wire][setting]


def wire_reasoning(
    wire: WireFormat,
    configured: ReasoningSetting | None,
    support: ReasoningSupport | None,
    requested: RequestReasoning | None = None,
) -> ReasoningWireValue | None:
    """The reasoning field value one request sends, or `None` to leave it out."""
    setting = request_reasoning(wire, configured, support, requested)
    return None if setting is None else REASONING_WIRE_VALUES[wire][setting]


def thinking_budget_fits(max_tokens: int) -> bool:
    """Whether a request's `max_tokens` leaves room for the smallest thinking budget."""
    return max_tokens > MIN_THINKING_BUDGET_TOKENS


def thinking_budget_tokens(level: ReasoningLevel, max_tokens: int) -> int:
    """The thinking budget of a level, kept below the request's `max_tokens`."""
    return max(
        MIN_THINKING_BUDGET_TOKENS, min(THINKING_BUDGET_TOKENS[level], max_tokens - 1)
    )


def anthropic_thinking_fields(
    setting: ReasoningSetting | None, support: ReasoningSupport | None, max_tokens: int
) -> dict[str, dict[str, object]]:
    """The thinking fields of an Anthropic request.

    Off disables thinking on a model known to reason, since some models think when
    the field is left out; one that cannot stop gets it too, so its API refuses the
    setting rather than the model thinking under an off. On a model whose reasoning
    is unknown, off sends no field. Models that take a budget get one below
    `max_tokens`; the others think adaptively at the setting's effort, with a
    summary of their thinking. A budget model whose `max_tokens` leaves no room for
    the smallest budget does not think.
    """
    if setting == REASONING_OFF and support is not None and support.levels:
        return {"thinking": {"type": "disabled"}}
    if setting is None or setting == REASONING_OFF:
        return {}
    level = cast(ReasoningLevel, setting)
    if uses_budget_tokens(support):
        if not thinking_budget_fits(max_tokens):
            return {}
        budget = thinking_budget_tokens(level, max_tokens)
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    return {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": REASONING_WIRE_VALUES[WireFormat.ANTHROPIC][level]},
    }


def uses_budget_tokens(support: ReasoningSupport | None) -> bool:
    """Whether the model takes a thinking budget rather than an effort."""
    return support is not None and support.control is ReasoningControl.BUDGET_TOKENS
