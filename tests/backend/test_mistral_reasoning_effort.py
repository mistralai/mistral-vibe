from __future__ import annotations

import pytest

from vibe.core.config import ModelConfig
from vibe.core.llm.backend.mistral import _resolve_reasoning_effort


def _model(**overrides: object) -> ModelConfig:
    base: dict[str, object] = {
        "name": "zai-glm-5-3",
        "provider": "mistral",
        "alias": "glm",
    }
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("thinking", "expected"),
    [
        ("off", None),
        ("low", "none"),
        ("medium", "high"),
        ("high", "high"),
        ("max", "high"),
    ],
)
def test_default_mapping_is_unchanged(thinking: str, expected: str | None) -> None:
    assert _resolve_reasoning_effort(_model(thinking=thinking)) == expected


def test_unsupported_effort_is_dropped() -> None:
    # glm-5-3 rejects "none": its supported values are low, high and max.
    model = _model(thinking="low", supported_reasoning_efforts=("low", "high", "max"))
    assert _resolve_reasoning_effort(model) is None


def test_supported_effort_is_kept() -> None:
    model = _model(thinking="high", supported_reasoning_efforts=("low", "high", "max"))
    assert _resolve_reasoning_effort(model) == "high"


def test_empty_support_list_drops_every_effort() -> None:
    model = _model(thinking="high", supported_reasoning_efforts=())
    assert _resolve_reasoning_effort(model) is None
