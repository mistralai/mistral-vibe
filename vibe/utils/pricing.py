from __future__ import annotations


def session_token_cost(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    input_price_per_million: float,
    output_price_per_million: float,
    cached_input_price_per_million: float | None,
) -> float:
    """Session cost in dollars, discounting cached prompt tokens.

    Cached tokens are billed at cached_input_price_per_million when set, otherwise
    at the input rate. cached_tokens is clamped to prompt_tokens: providers are not
    required to keep cached a subset of prompt, and an over-count must not yield a
    negative cost.
    """
    cached = min(cached_tokens, prompt_tokens)
    cached_price = (
        input_price_per_million
        if cached_input_price_per_million is None
        else cached_input_price_per_million
    )
    input_cost = (
        prompt_tokens - cached
    ) * input_price_per_million + cached * cached_price
    output_cost = completion_tokens * output_price_per_million
    return (input_cost + output_cost) / 1_000_000
