"""The completion profile of one model route of this Runtime.

A Host compiles each route once per session: its explicit settings, plus what the
selected adapter can send and report, give the model facts Core reads. The adapter
table below is the only place this Runtime states its capabilities.
"""

from __future__ import annotations

from collections.abc import Iterable

from mistralai_vibe_local_harness.protocol import (
    RustFixedImageCostRule,
    RustInputModality,
    RustModelFacts,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
)
from mistralai_vibe_local_harness.vibe.completion_profile import (
    TEXT_AND_IMAGE,
    AdapterCapabilities,
    CompiledProfile,
    ExplicitFacts,
    InputModality,
    ModelFacts,
    ProfileError,
    WireFormat,
    compile_profile,
)

# The wire format each generic ``api_style`` speaks.
_GENERIC_WIRES: dict[str, WireFormat] = {
    "openai": WireFormat.OPENAI_CHAT,
    "reasoning": WireFormat.OPENAI_CHAT,
    "openai-responses": WireFormat.OPENAI_RESPONSES,
    "anthropic": WireFormat.ANTHROPIC,
    "vertex-anthropic": WireFormat.ANTHROPIC,
}


def generic_wire(api_style: str) -> WireFormat:
    """The wire format a generic adapter's ``api_style`` speaks."""
    try:
        return _GENERIC_WIRES[api_style]
    except KeyError:
        raise ProfileError(
            f"no generic completion adapter for API style {api_style!r}"
        ) from None


def adapter_capabilities(config: LocalRuntimeAdapterConfig) -> AdapterCapabilities:
    """What the adapter ``config`` selects can send and report.

    Every adapter sends user images and reads token usage. None sends a response
    format. Only the Anthropic format requires ``max_tokens``. A cutoff is seen only
    when the provider sends a finish reason.
    """
    if config.backend == "mistral":
        wire = WireFormat.MISTRAL
    elif config.backend == "generic" and config.api_style in _GENERIC_WIRES:
        wire = generic_wire(config.api_style)
    else:
        raise ProfileError(
            f"no completion adapter for backend {config.backend!r} "
            f"with API style {config.api_style!r}"
        )
    return AdapterCapabilities(
        wire=wire,
        input_modalities=TEXT_AND_IMAGE,
        reports_cutoff=config.emits_finish_reason,
        reports_usage=True,
        structured_output=False,
        max_tokens_required=wire is WireFormat.ANTHROPIC,
    )


def compile_route_profile(
    config: LocalRuntimeAdapterConfig,
    explicit: ExplicitFacts,
    route: LocalModelRoute | None = None,
) -> CompiledProfile:
    """Compile the profile of ``route`` (the active one by default) on ``config``."""
    model = (route or config.active_model).model
    return compile_profile(model, adapter_capabilities(config), explicit)


def _rust_input_modality(modality: InputModality) -> RustInputModality:
    match modality:
        case InputModality.TEXT:
            return "text"
        case InputModality.IMAGE:
            return "image"


def rust_input_modalities(
    modalities: Iterable[InputModality],
) -> list[RustInputModality]:
    """The input modalities as Core reads them."""
    return [_rust_input_modality(modality) for modality in modalities]


def rust_model_facts(facts: ModelFacts) -> RustModelFacts:
    """The model facts as Core reads them."""
    return RustModelFacts(
        context_window=facts.context_window,
        compaction_threshold=facts.compaction_threshold,
        compaction_threshold_max=facts.compaction_threshold_max,
        input_modalities=rust_input_modalities(facts.input_modalities),
        image_cost=RustFixedImageCostRule(tokens=facts.image_cost.tokens),
    )


def default_model_facts() -> RustModelFacts:
    """The facts of the default route, for a config no Host has set up yet."""
    config = LocalRuntimeAdapterConfig()
    compiled = compile_route_profile(config, ExplicitFacts())
    return rust_model_facts(compiled.profile.facts)


__all__ = [
    "adapter_capabilities",
    "compile_route_profile",
    "default_model_facts",
    "generic_wire",
    "rust_input_modalities",
    "rust_model_facts",
]
