from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {
        "runtime": {
            "stats": {
                "steps": 42,
                "contextTokens": 12_345,
                "sessionPromptTokens": 1_234_567,
                "sessionCompletionTokens": 45_678,
                "sessionCachedTokens": 800_987,
                "lastTurnPromptTokens": 98_000,
                "lastTurnCompletionTokens": 765,
                "lastTurnCachedTokens": 12_345,
            }
        }
    }
}

timeline: Timeline = ["/status\r"]

request_methods = {"providerAuth/read"}

screen_contains = {
    "rust": (
        "Steps: 42",
        "Session Prompt Tokens: 1.23M (1,234,567)",
        "including 801k (800,987) cached",
        "Session Completion Tokens: 45.7k (45,678)",
        "Session Total LLM Tokens: 1.28M (1,280,245)",
        "Last Turn Tokens: 98.8k (98,765)",
        "including 12.3k (12,345) cached",
        "12.3k/600k tokens (2%)",
    )
}
