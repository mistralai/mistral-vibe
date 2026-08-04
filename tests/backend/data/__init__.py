from __future__ import annotations

Url = str
JsonResponse = dict
ResultData = dict
Chunk = bytes

# Shared usage every provider's `answer()` mock reports
ANSWER_PROMPT_TOKENS = 10
ANSWER_COMPLETION_TOKENS = 5
ANSWER_CONTEXT_TOKENS = ANSWER_PROMPT_TOKENS + ANSWER_COMPLETION_TOKENS
