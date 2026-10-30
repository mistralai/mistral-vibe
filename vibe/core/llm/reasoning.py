from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.config_values import THINKING_LEVELS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mistralai_vibe_local_harness.vibe.completion_profile import (
        CompiledProfile,
        ReasoningLevel,
        ReasoningSupport,
    )
    from vibe.config_values import ThinkingLevel
    from vibe.core.config import ModelConfig

__all__ = ["lowest_first", "profile_thinking", "written_reasoning_levels"]


def written_reasoning_levels(model: ModelConfig) -> ReasoningSupport | None:
    """The levels the config lists, as `ExplicitFacts.reasoning_levels` takes them.

    A set with no level, only off, says the user wants no reasoning, not that the
    model has none: the catalogue or the known row still says whether off has to
    be sent for the model to stop.
    """
    # Imported here: the Harness pulls the provider SDKs, which stay off the
    # CLI startup path.
    from mistralai_vibe_local_harness.vibe.completion_profile import ReasoningSupport

    # Every config layer fills the field, so only a set other than the default
    # five says which levels the model takes.
    if not model.thinking_levels_curated:
        return None
    levels = lowest_first([level for level in model.thinking_levels if level != "off"])
    if not levels:
        return None
    return ReasoningSupport(levels, can_turn_off="off" in model.thinking_levels)


def lowest_first(levels: Sequence[str]) -> tuple[ReasoningLevel, ...]:
    """The standard levels take their rank within their own slots; a level of the
    model's own stays where its source put it.
    """
    ranked = iter(
        sorted(
            (level for level in levels if level in THINKING_LEVELS),
            key=THINKING_LEVELS.index,
        )
    )
    return tuple(
        next(ranked) if level in THINKING_LEVELS else level for level in levels
    )


def profile_thinking(compiled: CompiledProfile) -> ThinkingLevel:
    """The level the compile kept, as written, else the model's lowest setting.

    The lowest is off where the model can stop or its levels are unknown, else its
    lowest level: an unset level must not ask a model that always reasons for off.
    """
    if (written := compiled.profile.format.reasoning_setting) is not None:
        return written
    support = compiled.profile.format.reasoning
    if support is None or support.can_turn_off:
        return "off"
    return support.levels[0]
