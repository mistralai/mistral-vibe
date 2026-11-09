# Twin: mistralai.vibe.harness.runtime.completion.profile.compile. Keep both copies identical below this line.
"""Compile one completion profile from the four layers, field by field.

For each field the first layer with a value wins: explicit, catalogue, known model,
default. The catalogue never sets the threshold. Input modalities and reasoning are then cut to
what the adapter can send. Warnings are returned as data; the host routes them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .bounds import (
    DEFAULT_CONTEXT_WINDOW,
    REQUIRED_MAX_TOKENS_FALLBACK,
    check_threshold,
    threshold_bounds,
)
from .known_models import KNOWN_MODELS, KnownModelRow, find_known_model
from .reasoning import (
    MIN_THINKING_BUDGET_TOKENS,
    THINKING_BUDGET_TOKENS,
    parse_reasoning,
    thinking_budget_fits,
    uses_budget_tokens,
)
from .types import (
    REASONING_OFF,
    TEXT_ONLY,
    AdapterCapabilities,
    CatalogueEntry,
    CompletionProfile,
    CompletionWarning,
    ExplicitFacts,
    FixedImageCost,
    InputModality,
    KnownModel,
    ModelFacts,
    ProfileError,
    ProviderFormat,
    ReasoningSetting,
    ReasoningSupport,
    Source,
    WarningCode,
)


@dataclass(frozen=True, slots=True)
class CompiledProfile:
    """A profile and the warnings its compile raised."""

    profile: CompletionProfile
    warnings: tuple[CompletionWarning, ...]


def compile_profile(
    model: str,
    adapter: AdapterCapabilities,
    explicit: ExplicitFacts,
    catalogue: CatalogueEntry | None = None,
    known_models: Sequence[KnownModelRow] = KNOWN_MODELS,
) -> CompiledProfile:
    """Compile the profile of one model route, or raise `ProfileError`."""
    if not adapter.reports_usage:
        raise ProfileError(
            f"the {adapter.wire} adapter cannot report token usage, "
            "which every completion needs"
        )
    entry = catalogue or CatalogueEntry()
    known = find_known_model(model, known_models) or KnownModel()
    warnings: list[CompletionWarning] = []
    sources: dict[str, Source] = {}

    window, sources["context_window"] = _first(
        explicit.context_window,
        _usable_catalogue_window(model, entry.context_window, warnings),
        known.context_window,
        default=DEFAULT_CONTEXT_WINDOW,
    )
    if sources["context_window"] is Source.DEFAULT:
        warnings.append(
            CompletionWarning(
                WarningCode.WINDOW_GUESSED,
                model,
                f"the context window of {model} is unknown; assuming {window} tokens",
            )
        )
    bounds = threshold_bounds(window)
    threshold, sources["compaction_threshold"] = bounds.default, Source.DEFAULT
    if explicit.compaction_threshold is not None:
        threshold = check_threshold(explicit.compaction_threshold, bounds, window)
        sources["compaction_threshold"] = Source.EXPLICIT

    max_output, sources["max_output_tokens"] = _first(
        _explicit_max_output(explicit.max_output_tokens, window),
        _usable_catalogue_max_output(model, entry.max_output_tokens, window, warnings),
        _below(known.max_output_tokens, window),
        default=None,
    )
    model_modalities, sources["input_modalities"] = _first(
        explicit.input_modalities,
        entry.input_modalities,
        known.input_modalities,
        default=TEXT_ONLY,
    )
    support, sources["reasoning"] = _first(
        _written_levels(explicit.reasoning_levels, entry.reasoning, known.reasoning),
        entry.reasoning,
        known.reasoning,
        default=None,
    )
    setting = _reasoning_setting(explicit.reasoning, explicit.reasoning_levels, adapter)

    wire_max_tokens = (
        (explicit.max_tokens or max_output or REQUIRED_MAX_TOKENS_FALLBACK)
        if adapter.max_tokens_required
        else None
    )
    budgeted = setting not in (None, REASONING_OFF) and uses_budget_tokens(support)
    if budgeted and setting not in THINKING_BUDGET_TOKENS:
        warnings.append(
            CompletionWarning(
                WarningCode.REASONING_BUDGET_UNAVAILABLE,
                model,
                f"{model} takes a thinking budget, and {setting!r} has none; its "
                "requests go without reasoning. Pick one of "
                f"{', '.join(THINKING_BUDGET_TOKENS)}.",
            )
        )
    elif (
        budgeted
        and wire_max_tokens is not None
        and not thinking_budget_fits(wire_max_tokens)
    ):
        warnings.append(
            CompletionWarning(
                WarningCode.REASONING_BUDGET_UNAVAILABLE,
                model,
                f"{model} needs max_tokens above {MIN_THINKING_BUDGET_TOKENS} to "
                f"reason; its requests send {wire_max_tokens}, so they go without "
                "reasoning",
            )
        )

    facts = ModelFacts(
        context_window=window,
        compaction_threshold=threshold,
        compaction_threshold_max=bounds.maximum,
        max_output_tokens=max_output,
        input_modalities=_accepted_modalities(
            model_modalities, adapter.input_modalities
        ),
        image_cost=FixedImageCost(),
    )
    provider_format = ProviderFormat(
        wire=adapter.wire,
        reasoning=support,
        reasoning_setting=setting,
        wire_max_tokens=wire_max_tokens,
        structured_output=adapter.structured_output,
    )
    return CompiledProfile(
        profile=CompletionProfile(
            model=model, facts=facts, format=provider_format, sources=sources
        ),
        warnings=tuple(warnings),
    )


def _accepted_modalities(
    model: tuple[InputModality, ...], adapter: tuple[InputModality, ...]
) -> tuple[InputModality, ...]:
    """What both the model and the adapter take, in declaration order; text always."""
    return tuple(
        modality
        for modality in InputModality
        if modality is InputModality.TEXT or (modality in model and modality in adapter)
    )


def _written_levels(
    written: ReasoningSupport | None,
    catalogue: ReasoningSupport | None,
    known: ReasoningSupport | None,
) -> ReasoningSupport | None:
    """The user's levels, sent the way the catalogue or the table says the model
    takes reasoning: a token budget or an effort.
    """
    if written is None:
        return None
    described = catalogue or known
    if described is None:
        return written
    return replace(written, control=described.control)


def _first[T, D](
    explicit: T | None, catalogue: T | None, known: T | None, *, default: D
) -> tuple[T | D, Source]:
    """Return the first known value and the layer it came from."""
    for value, source in (
        (explicit, Source.EXPLICIT),
        (catalogue, Source.CATALOGUE),
        (known, Source.KNOWN_MODEL),
    ):
        if value is not None:
            return value, source
    return default, Source.DEFAULT


def _usable_catalogue_window(
    model: str, window: int | None, warnings: list[CompletionWarning]
) -> int | None:
    """Drop a catalogue window the bounds reject; the next layer fills it."""
    if window is None:
        return None
    try:
        threshold_bounds(_positive_or_none(window) or 0)
    except ProfileError:
        warnings.append(
            CompletionWarning(
                WarningCode.CATALOGUE_UNAVAILABLE,
                model,
                f"the catalogue's context window {window!r} for {model} is unusable; "
                "ignoring it",
            )
        )
        return None
    return window


def _explicit_max_output(value: int | None, window: int) -> int | None:
    """An explicit max output must leave room for input in the window."""
    if value is not None and value >= window:
        raise ProfileError(
            f"a max output of {value} tokens does not fit a context window of "
            f"{window} tokens; it must be smaller"
        )
    return value


def _usable_catalogue_max_output(
    model: str, value: int | None, window: int, warnings: list[CompletionWarning]
) -> int | None:
    """Drop a catalogue max output that does not fit the window; the next layer fills it."""
    positive = _positive_or_none(value)
    if positive is None or positive < window:
        return positive
    warnings.append(
        CompletionWarning(
            WarningCode.CATALOGUE_UNAVAILABLE,
            model,
            f"the catalogue's max output {positive} for {model} does not fit its "
            f"{window}-token window; ignoring it",
        )
    )
    return None


def _below(value: int | None, window: int) -> int | None:
    """The value when it is below the window, else unknown.

    A known model's max output can outgrow a narrower explicit or catalogue window.
    """
    return value if value is not None and value < window else None


def _positive_or_none(value: int | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _reasoning_setting(
    raw: str | None, listed: ReasoningSupport | None, adapter: AdapterCapabilities
) -> ReasoningSetting | None:
    """Parse the user's setting, whatever the word; it goes out as written, and the
    provider judges it. A level the config lists wins over the format's own names,
    so a listed `minimal` is not sent as `low`.
    """
    if raw is None:
        return None
    if listed is not None and (written := raw.strip()) in listed.levels:
        return written
    return parse_reasoning(raw, adapter.wire)
