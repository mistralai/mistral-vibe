"""Semantic Conventions for Mistral Vibe Observability.

This module centralizes all semantic convention constants following OpenTelemetry
GenAI semantic conventions where applicable.

References:
- https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/
- https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
"""

from __future__ import annotations

# =============================================================================
# METRIC NAMES
# =============================================================================

# GenAI Client Metrics
METRIC_GENAI_CLIENT_TOKEN_USAGE = "gen_ai.client.token.usage"
METRIC_GENAI_CLIENT_OPERATION_DURATION = "gen_ai.client.operation.duration"

# Agent Metrics
METRIC_AGENT_EXECUTION_COUNT = "agent.execution.count"
METRIC_AGENT_EXECUTION_DURATION = "agent.execution.duration"

# Tool Metrics
METRIC_TOOL_EXECUTION_COUNT = "tool.execution.count"
METRIC_TOOL_EXECUTION_DURATION = "tool.execution.duration"

# System Metrics
METRIC_SYSTEM_MEMORY_USAGE = "system.memory.usage"
METRIC_SYSTEM_CPU_USAGE = "system.cpu.usage"


# =============================================================================
# SPAN ATTRIBUTE
# =============================================================================

# GenAI Common Attributes
ATTR_GEN_AI_SYSTEM = "gen_ai.system"
ATTR_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"

# GenAI Request Attributes
ATTR_GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
ATTR_GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
ATTR_GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"

# GenAI Response Attributes
ATTR_GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"

# GenAI Token Attributes (for metrics)
ATTR_GEN_AI_TOKEN_TYPE = "gen_ai.token.type"

# GenAI Usage Attributes (for spans)
ATTR_GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
ATTR_GEN_AI_USAGE_DURATION_MS = "gen_ai.usage.duration_ms"


# =============================================================================
# LLM REQUEST ATTRIBUTES
# =============================================================================

ATTR_LLM_STREAMING = "llm.streaming"
ATTR_LLM_ENDPOINT = "llm.endpoint"
ATTR_LLM_REQUEST_TOOL_COUNT = "llm.request.tool_count"
ATTR_LLM_REQUEST_HAS_TOOLS = "llm.request.has_tools"


# =============================================================================
# TOOL ATTRIBUTES
# =============================================================================

ATTR_TOOL_NAME = "tool.name"
ATTR_TOOL_TYPE = "tool.type"
ATTR_TOOL_COMMAND = "tool.command"
ATTR_TOOL_DURATION_MS = "tool.duration_ms"


# =============================================================================
# ERROR ATTRIBUTES
# =============================================================================

ATTR_ERROR = "error"
ATTR_ERROR_MESSAGE = "error.message"


# =============================================================================
# ATTRIBUTE VALUES
# =============================================================================

# gen_ai.system values
SYSTEM_MISTRAL_VIBE = "mistral-vibe"
SYSTEM_MISTRAL = "mistral"
SYSTEM_OPENAI = "openai"

# gen_ai.operation.name values
OP_CHAT = "chat"
OP_AGENT_EXECUTION = "agent.execution"
OP_TOOL_EXECUTION = "tool.execution"
OP_LLM_REQUEST = "llm.request"

# gen_ai.token.type values
TOKEN_TYPE_INPUT = "input"
TOKEN_TYPE_OUTPUT = "output"


# =============================================================================
# DEFAULT SPAN ATTRIBUTES
# =============================================================================

def get_agent_execution_attributes() -> dict[str, str]:
    """Get default attributes for agent execution spans."""
    return {
        ATTR_GEN_AI_SYSTEM: SYSTEM_MISTRAL_VIBE,
        ATTR_GEN_AI_OPERATION_NAME: OP_AGENT_EXECUTION,
    }


def get_tool_execution_attributes() -> dict[str, str]:
    """Get default attributes for tool execution spans."""
    return {
        ATTR_GEN_AI_SYSTEM: SYSTEM_MISTRAL_VIBE,
        ATTR_GEN_AI_OPERATION_NAME: OP_TOOL_EXECUTION,
    }


def get_llm_request_attributes() -> dict[str, str]:
    """Get default attributes for LLM request spans."""
    return {
        ATTR_GEN_AI_SYSTEM: SYSTEM_MISTRAL_VIBE,
        ATTR_GEN_AI_OPERATION_NAME: OP_LLM_REQUEST,
    }
