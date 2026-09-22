"""Provider view passed to generic adapters.

The view carries the subset built from ``LocalRuntimeAdapterConfig`` that
provider adapters need.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProviderView:
    name: str
    api_base: str
    api_style: str = "openai"
    reasoning_field_name: str = "reasoning_content"
    emits_finish_reason: bool = True
    project_id: str = ""
    region: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)


__all__ = ["ProviderView"]
