from __future__ import annotations

from pydantic import JsonValue

from vibe.app_server.models import (
    BlockedEffectState,
    CancelledEffectState,
    CompletedEffectState,
    EffectState,
    FailedEffectState,
    PublicEffectEntry,
    PublicHistoryEntry,
    RunningEffectState,
)

# The Harness's hard inline limit (25k tokens at 4 characters each): the model
# never sees more of one tool output than this, so neither does a client.
EFFECT_OUTPUT_TEXT_MAX_CHARS = 100_000
# A structured output whose strings are each capped can still be huge (a list
# of a million short strings); past this budget it is dropped and clients
# render the capped ``output_text`` instead.
EFFECT_OUTPUT_MAX_CHARS = 2 * EFFECT_OUTPUT_TEXT_MAX_CHARS
TRUNCATION_MARKER = "\n... [truncated]"


def capped_effect_entry[HistoryEntryT: PublicHistoryEntry](
    entry: HistoryEntryT,
) -> HistoryEntryT:
    """Bound the tool output a public effect entry carries to its clients.

    Every response and notification is one JSON-RPC frame, so an unbounded
    tool output (an MCP tool dumping megabytes) makes frames clients reject.
    Entries within the limits come back as the same object.
    """
    if not isinstance(entry, PublicEffectEntry):
        return entry
    state = capped_effect_state(entry.state)
    if state is entry.state:
        return entry
    return entry.model_copy(update={"state": state})


def capped_effect_state(state: EffectState) -> EffectState:
    match state:
        case CompletedEffectState() | FailedEffectState():
            output_text = _capped_text(state.output_text)
            output = _capped_output(state.output)
            if output_text is state.output_text and output is state.output:
                return state
            return state.model_copy(
                update={"output": output, "output_text": output_text}
            )
        case RunningEffectState() | BlockedEffectState() | CancelledEffectState():
            output_text = _capped_text(state.output_text)
            if output_text is state.output_text:
                return state
            return state.model_copy(update={"output_text": output_text})
        case _:
            return state


def capped_output_append(current: str, text: str) -> str:
    """The part of a streamed chunk ``current`` output may still append."""
    if len(current) > EFFECT_OUTPUT_TEXT_MAX_CHARS and current.endswith(
        TRUNCATION_MARKER
    ):
        return ""
    room = max(EFFECT_OUTPUT_TEXT_MAX_CHARS - len(current), 0)
    if len(text) <= room:
        return text
    return f"{text[:room]}{TRUNCATION_MARKER}"


def _capped_text(text: str) -> str:
    # The marker's allowance keeps capping idempotent: a capped text is
    # returned as is instead of being cut and re-marked on every projection.
    if len(text) <= EFFECT_OUTPUT_TEXT_MAX_CHARS + len(TRUNCATION_MARKER):
        return text
    return f"{text[:EFFECT_OUTPUT_TEXT_MAX_CHARS]}{TRUNCATION_MARKER}"


def _capped_output(output: JsonValue) -> JsonValue:
    capped, size = _capped_json(output)
    if size > EFFECT_OUTPUT_MAX_CHARS:
        return None
    return capped


def _capped_json(value: JsonValue) -> tuple[JsonValue, int]:
    """Cap every string in ``value``; also return its approximate size in chars."""
    match value:
        case str():
            capped = _capped_text(value)
            return capped, len(capped)
        case list():
            items = [_capped_json(item) for item in value]
            size = len(value) + sum(item_size for _, item_size in items)
            if all(
                capped is item for (capped, _), item in zip(items, value, strict=True)
            ):
                return value, size
            return [capped for capped, _ in items], size
        case dict():
            fields = {key: _capped_json(item) for key, item in value.items()}
            size = sum(len(key) + item_size for key, (_, item_size) in fields.items())
            if all(fields[key][0] is item for key, item in value.items()):
                return value, size
            return {key: capped for key, (capped, _) in fields.items()}, size
        case _:
            return value, 1
