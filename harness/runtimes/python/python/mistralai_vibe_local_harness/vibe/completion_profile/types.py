# Twin: mistralai.vibe.harness.runtime.completion.profile.types. Keep both copies identical below this line.
"""Types of a completion profile: every fact about one model on one provider format.

A profile is compiled once per model route from four layers (explicit settings, the
provider's catalogue, the known-models table, defaults). `ModelFacts` is what the
Core reads; `ProviderFormat` is what the adapter reads. `None` always means unknown,
never zero.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

DEFAULT_IMAGE_TOKENS = 8_192


class ProfileError(ValueError):
    """A completion config that cannot compile into a profile."""


class InputModality(StrEnum):
    """A kind of input a model reads."""

    TEXT = "text"
    IMAGE = "image"


TEXT_ONLY: tuple[InputModality, ...] = (InputModality.TEXT,)
TEXT_AND_IMAGE: tuple[InputModality, ...] = (InputModality.TEXT, InputModality.IMAGE)


# A level is any word the provider takes: a standard one, or a model's own that
# its config or catalogue lists. Either goes out as written.
type ReasoningLevel = str
type ReasoningSetting = str

REASONING_OFF: ReasoningSetting = "off"
# The standard levels, ascending: a level the model lacks rounds down along this
# order. A level outside it has no rank, so nothing rounds to it.
REASONING_LEVELS: tuple[ReasoningLevel, ...] = ("low", "medium", "high", "xhigh", "max")
REASONING_SETTINGS: tuple[ReasoningSetting, ...] = (REASONING_OFF, *REASONING_LEVELS)
# What one request of the Harness's own (a compaction, say) asks for: a standard
# setting, or the least reasoning the model takes, which is off where it can stop.
type RequestReasoning = ReasoningSetting | Literal["lowest"]
REASONING_LOWEST: Final = "lowest"


class Source(StrEnum):
    """The layer a profile value came from."""

    EXPLICIT = "explicit"
    CATALOGUE = "catalogue"
    KNOWN_MODEL = "known_model"
    DEFAULT = "default"


class WireFormat(StrEnum):
    """The HTTP format one adapter speaks."""

    MISTRAL = "mistral"
    OPENAI_CHAT = "openai_chat"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class ReasoningControl(StrEnum):
    """How a model takes its reasoning level; the adapter maps it to the wire."""

    EFFORT = "effort"
    BUDGET_TOKENS = "budget_tokens"


@dataclass(frozen=True, slots=True)
class ReasoningSupport:
    """The reasoning levels one model accepts, lowest first, and whether it can stop.

    The levels keep the order their source lists them in, so a level outside the
    standard ones keeps its place. No levels means the model takes no reasoning
    setting at all, not even off.
    """

    levels: tuple[ReasoningLevel, ...]
    can_turn_off: bool
    control: ReasoningControl = ReasoningControl.EFFORT

    def __post_init__(self) -> None:
        if not self.levels and not self.can_turn_off:
            raise ProfileError("a model that always reasons has at least one level")
        if REASONING_OFF in self.levels or not all(map(str.strip, self.levels)):
            raise ProfileError(
                "reasoning levels are non-empty words other than off, which "
                f"`can_turn_off` states: {list(self.levels)}"
            )
        if len(set(self.levels)) != len(self.levels):
            raise ProfileError(f"reasoning levels must be unique: {list(self.levels)}")


# A model without reasoning: its API rejects every reasoning setting, off included.
NO_REASONING: Final = ReasoningSupport((), can_turn_off=True)


@dataclass(frozen=True, slots=True)
class FixedImageCost:
    """Every image counts as the same number of input tokens."""

    tokens: int = DEFAULT_IMAGE_TOKENS

    def __post_init__(self) -> None:
        _require_positive("image tokens", self.tokens)


# The only rule that ships; rules by pixels or tiles keep the same shape.
type ImageCostRule = FixedImageCost


@dataclass(frozen=True, slots=True)
class ModelFacts:
    """What the Core reads about the model; no provider names, no wire fields."""

    context_window: int
    compaction_threshold: int
    compaction_threshold_max: int
    max_output_tokens: int | None
    # The kinds of input the model and its adapter accept; text is always one.
    input_modalities: tuple[InputModality, ...]
    image_cost: ImageCostRule = field(default_factory=FixedImageCost)

    def __post_init__(self) -> None:
        _require_positive("context_window", self.context_window)
        _require_positive("compaction_threshold", self.compaction_threshold)
        _require_positive("compaction_threshold_max", self.compaction_threshold_max)
        _require_optional_positive("max_output_tokens", self.max_output_tokens)
        _require_modalities(self.input_modalities)
        if not (
            self.compaction_threshold
            <= self.compaction_threshold_max
            < self.context_window
        ):
            raise ProfileError(
                "compaction_threshold <= compaction_threshold_max < context_window "
                f"does not hold: {self.compaction_threshold}, "
                f"{self.compaction_threshold_max}, {self.context_window}"
            )


@dataclass(frozen=True, slots=True)
class ProviderFormat:
    """What only the adapter reads: how this model is spoken to on the wire."""

    wire: WireFormat
    reasoning: ReasoningSupport | None
    reasoning_setting: ReasoningSetting | None
    wire_max_tokens: int | None
    structured_output: bool

    def __post_init__(self) -> None:
        _require_optional_positive("wire_max_tokens", self.wire_max_tokens)


@dataclass(frozen=True, slots=True)
class CompletionProfile:
    """One compiled profile per model route, with the source of each value."""

    model: str
    facts: ModelFacts
    format: ProviderFormat
    sources: Mapping[str, Source]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))


class WarningCode(StrEnum):
    """Warnings a host shows its user; each one is data, never an exception."""

    WINDOW_GUESSED = "window_guessed"
    CATALOGUE_UNAVAILABLE = "catalogue_unavailable"
    REASONING_BUDGET_UNAVAILABLE = "reasoning_budget_unavailable"
    IMAGES_REPLACED = "images_replaced"


@dataclass(frozen=True, slots=True)
class CompletionWarning:
    """One warning about one subject (a model or a catalogue endpoint)."""

    code: WarningCode
    subject: str
    message: str


@dataclass(frozen=True, slots=True)
class ExplicitFacts:
    """Layer 1: what the user wrote. `None` is unknown; 0 or below is rejected."""

    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: tuple[InputModality, ...] | None = None
    compaction_threshold: int | None = None
    reasoning: str | None = None
    # The levels the user listed for the model. They win over the catalogue and
    # the known-model table, which can lag a model that changed under its name;
    # how reasoning is sent (budget or effort) still comes from those.
    reasoning_levels: ReasoningSupport | None = None
    # The `max_tokens` every request sends, where the user pinned one.
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_optional_positive("context_window", self.context_window)
        _require_optional_positive("max_output_tokens", self.max_output_tokens)
        _require_optional_positive("compaction_threshold", self.compaction_threshold)
        _require_optional_positive("max_tokens", self.max_tokens)


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """Layer 2: what the provider's catalogue said about the model."""

    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: tuple[InputModality, ...] | None = None
    reasoning: ReasoningSupport | None = None


@dataclass(frozen=True, slots=True)
class KnownModel:
    """Layer 3: what the shipped table knows about the model."""

    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: tuple[InputModality, ...] | None = None
    reasoning: ReasoningSupport | None = None


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """What one adapter can send and report, whatever the model can do."""

    wire: WireFormat
    input_modalities: tuple[InputModality, ...]
    reports_cutoff: bool
    reports_usage: bool
    structured_output: bool
    max_tokens_required: bool


def _require_modalities(modalities: tuple[InputModality, ...]) -> None:
    if InputModality.TEXT not in modalities:
        raise ProfileError(
            f"input_modalities must include text, got {list(modalities)!r}"
        )
    if len(set(modalities)) != len(modalities):
        raise ProfileError(
            f"input_modalities must not repeat a modality, got {list(modalities)!r}"
        )


def _require_positive(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProfileError(f"{name} must be a positive integer, got {value!r}")


def _require_optional_positive(name: str, value: int | None) -> None:
    if value is not None:
        _require_positive(name, value)
