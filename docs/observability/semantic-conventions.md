# Observability Semantic Conventions

Semantic conventions for metrics and trace attributes in Mistral Vibe, following [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

## Metrics

### GenAI Client Metrics

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `gen_ai.client.token.usage` | Counter | `token` | Token consumption per LLM request |
| `gen_ai.client.operation.duration` | Histogram | `s` | LLM operation duration |

**Attributes for token.usage:**
- `gen_ai.token.type`: `input` or `output`
- `gen_ai.response.model`: Model name
- `gen_ai.operation.name`: `chat`
- `gen_ai.system`: Provider (`mistral-vibe`)

### Agent/Tool Metrics

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `agent.execution.count` | Counter | `execution` | Agent execution count |
| `agent.execution.duration` | Histogram | `ms` | Agent execution duration |
| `tool.execution.count` | Counter | `execution` | Tool execution count |
| `tool.execution.duration` | Histogram | `ms` | Tool execution duration |

### System Metrics

| Metric | Type | Unit | Description |
|--------|------|------|-------------|
| `system.memory.usage` | ObservableGauge | `kB` | Process RSS memory |
| `system.cpu.usage` | ObservableGauge | `1` | 1-minute load average |

## Trace Span Attributes

### GenAI Common (all spans)

| Attribute | Type | Used In |
|-----------|------|---------|
| `gen_ai.system` | string | Agent, Tool, LLM spans |
| `gen_ai.operation.name` | string | Agent, Tool, LLM spans |

Values for `gen_ai.system`: `mistral-vibe`, `mistral`
Values for `gen_ai.operation.name`: `chat`, `agent.execution`, `tool.execution`, `llm.request`

### GenAI Request/Response (LLM spans)

| Attribute | Type | Description |
|-----------|------|-------------|
| `gen_ai.request.model` | string | Requested model |
| `gen_ai.request.temperature` | float | Temperature param |
| `gen_ai.request.max_tokens` | int | Max tokens param |
| `gen_ai.response.model` | string | Actual model used |

### GenAI Usage (spans)

| Attribute | Type | Used In | Description |
|-----------|------|---------|-------------|
| `gen_ai.usage.input_tokens` | int | LLM spans (generic backend only) | Input token count |
| `gen_ai.usage.output_tokens` | int | LLM spans (generic backend only) | Output token count |
| `gen_ai.usage.total_tokens` | int | LLM spans (generic backend only) | Total tokens |
| `gen_ai.usage.duration_ms` | float | Agent spans | Duration in ms |

> Note: `input_tokens`, `output_tokens`, `total_tokens` only appear when using OpenAI-compatible providers (generic backend). Mistral native SDK does not set these span attributes.

### LLM Request (LLM spans)

| Attribute | Type | Description |
|-----------|------|-------------|
| `llm.streaming` | bool | Streaming enabled |
| `llm.endpoint` | string | API endpoint URL |
| `llm.request.tool_count` | int | Tool count |
| `llm.request.has_tools` | bool | Has tools |

### Tool (Tool spans)

| Attribute | Type | Description |
|-----------|------|-------------|
| `tool.name` | string | Tool name |
| `tool.type` | string | Tool type/class |
| `tool.command` | string | Command (if applicable) |
| `tool.duration_ms` | float | Execution duration |

### Error (all spans)

| Attribute | Type | Description |
|-----------|------|-------------|
| `error` | bool | Error occurred |
| `error.message` | string | Error message |

### Input/Output (all spans)

| Attribute | Type | Description |
|-----------|------|-------------|
| `input-json` | string | JSON-serialized input data |
| `output-json` | string | JSON-serialized output data |

**Content by span type:**

| Span Type | `input-json` | `output-json` |
|-----------|--------------|---------------|
| AgentExecution | `{"prompt": "user message"}` | `{"response": "agent response"}` |
| ToolExecution | Tool kwargs | `{"result": ...}` |
| LLMRequest | `{"message_count": N, "tool_count": N, "streaming": bool}` | `{"content": "LLM response"}` |
| UserInteraction | `{"argv": [...], "agent": "..."}` | - |

## Token Data Source

Tokens extracted from LLM API response `usage` field:

```json
{"usage": {"prompt_tokens": 123, "completion_tokens": 456}}
```

- `prompt_tokens` → `gen_ai.client.token.usage` with `gen_ai.token.type=input`
- `completion_tokens` → `gen_ai.client.token.usage` with `gen_ai.token.type=output`

## Reference

All constants defined in `vibe/core/observability/semconv.py`.
