from types import SimpleNamespace

from vibe.core.observability.semconv import (
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MAX_TOKENS,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_LLM_REQUEST_TOOL_COUNT,
    ATTR_LLM_STREAMING,
    ATTR_TOOL_NAME,
    ATTR_TOOL_TYPE,
)
from vibe.core.observability.tracing import (
    _sanitize_metadata_payload,
    _tool_attribute_defaults,
    build_llm_request_attributes,
)


def test_sanitize_metadata_payload_handles_nested_objects():
    payload = {
        "command": "ls -la",
        "options": {"dry_run": False, "meta": SimpleNamespace(foo="bar")},
    }
    sanitized = _sanitize_metadata_payload(payload)
    assert sanitized["command"] == "ls -la"
    assert sanitized["options"]["dry_run"] is False
    assert isinstance(sanitized["options"]["meta"], str)


def test_tool_attribute_defaults_prefers_name_and_type():
    class DummyTool:
        name = None
        tool_type = "custom"

        def get_name(self):
            return "filesystem.read"

    attrs = _tool_attribute_defaults(DummyTool())
    assert attrs[ATTR_TOOL_NAME] == "filesystem.read"
    assert attrs[ATTR_TOOL_TYPE] == "custom"


def test_build_llm_request_attributes_sets_core_fields():
    attrs = build_llm_request_attributes(
        model_name="mistral-large",
        provider_name="mistral",
        temperature=0.3,
        max_tokens=1024,
        streaming=False,
        tool_count=2,
        endpoint="https://api.mistral.dev",
    )
    assert attrs[ATTR_GEN_AI_REQUEST_MODEL] == "mistral-large"
    assert attrs[ATTR_GEN_AI_SYSTEM] == "mistral"
    assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "chat"
    assert attrs[ATTR_GEN_AI_REQUEST_MAX_TOKENS] == 1024
    assert attrs[ATTR_LLM_STREAMING] is False
    assert attrs[ATTR_LLM_REQUEST_TOOL_COUNT] == 2
