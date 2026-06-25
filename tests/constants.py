from __future__ import annotations

MISTRAL_BASE_URL = "https://api.mistral.ai"
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
CONNECTORS_BOOTSTRAP_PATH = "/v1/connectors/bootstrap"

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_MESSAGES_PATH = "/v1/messages"

OPENAI_BASE_URL = "https://api.openai.com"
OPENAI_RESPONSES_PATH = "/v1/responses"

VERTEX_PROJECT_ID = "test-project"
VERTEX_REGION = "us-central1"
VERTEX_MODEL = "claude-test"
VERTEX_BASE_URL = "https://us-central1-aiplatform.googleapis.com"
VERTEX_RAW_PREDICT_PATH = (
    "/v1/projects/test-project/locations/us-central1/"
    "publishers/anthropic/models/claude-test:rawPredict"
)
VERTEX_STREAM_PREDICT_PATH = (
    "/v1/projects/test-project/locations/us-central1/"
    "publishers/anthropic/models/claude-test:streamRawPredict"
)

REASONING_BASE_URL = "https://api.reasoning.test"
REASONING_COMPLETIONS_PATH = "/chat/completions"

TELEPORT_SESSIONS_PATH = "/api/v1/code/sessions"
TELEPORT_COMPLETE_URL = "https://chat.example.com/code/project-id/web-session-id"
