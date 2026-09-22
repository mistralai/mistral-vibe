"""Projection from committed Harness activity to Session Protocol state."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import (
    RustAssistantMessageCommittedObservation,
    RustCompletedTurn,
    RustContextCompactedObservation,
    RustContextCompactionFailedObservation,
    RustFailedTurn,
    RustImageContentBlock,
    RustInterruptedTurn,
    RustLLMCallAction,
    RustProtocolError,
    RustProvidedToolCallAction,
    RustReasoningPart,
    RustReasoningSummaryContent,
    RustReasoningTextContent,
    RustRunningTurn,
    RustRuntimeBuiltinToolCallAction,
    RustSessionTransition,
    RustTextContentBlock,
    RustToolCallAction,
    RustToolDiscoveryFinishedObservation,
    RustToolFailureResult,
    RustToolResultCommittedObservation,
    RustToolSuccessResult,
    RustTurnCompletedObservation,
    RustTurnFailedObservation,
    RustTurnInterruptedObservation,
    RustTurnStartedObservation,
    RustTurnSteeredObservation,
    RustTurnSteeringReceivedObservation,
)
from mistralai_vibe_local_harness.session_protocol import (
    CompletedPublicTurn,
    FailedPublicTurn,
    FailedSessionStatus,
    BlockedSessionStatus,
    IdleSessionStatus,
    InProgressPublicTurn,
    InterruptedPublicTurn,
    JsonObject,
    PublicError,
    PublicSessionState,
    RunningSessionStatus,
    TitleSource,
    TokenUsage,
)
from mistralai_vibe_local_harness.vibe._observability import add_uncorrelated_tool_result
from mistralai_vibe_local_harness.vibe._runtime_config import CompletionDelta
from mistralai_vibe_local_harness.vibe._storage import (
    ProjectionDelta,
    ProjectionStateV1,
    compute_projection_delta,
)

_PREVIEW_MAX_CHARS = 200

# _meta key a pre_tool_call hook uses to ride an approval note out on a tool result;
# lifted here into the effect's ``display.warnings`` for the UI (never model-visible).
APPROVAL_NOTE_META_KEY = "approval_note"

# _meta key for the approval decision metadata (decision, approval_type,
# approval_source) that the local actions gate records on the tool result;
# lifted into the effect state for telemetry.
APPROVAL_META_KEY = "approval"


@dataclass(frozen=True, slots=True)
class ProjectionUpdate:
    projection: ProjectionStateV1
    event: JsonObject
    # ``None`` marks an in-memory-only advance: the snapshot and the event carry
    # it, the journal does not. Used for provisional completion content, which
    # the provider restates from the beginning on every retry and which the
    # committed observation supersedes, so persisting it buys nothing and costs
    # a write per fragment.
    delta: ProjectionDelta | None
    # What the journal holds once ``delta`` lands. ``projection.snapshot`` is the
    # on-screen view and carries provisional entries the store never sees, so a
    # persistence retry deciding whether the store already has this advance must
    # compare against this rather than against what the user is looking at.
    journaled_snapshot: PublicSessionState


class SessionProjector:
    """Builds the next public snapshot from the current one, without copying it.

    Every builder below reads ``self._projection.snapshot`` directly. The snapshot
    carries the whole transcript, so copying it per event costs the length of the
    session on every event; reading it is safe only because a builder replaces what
    it changes instead of mutating it -- entry dicts are rebuilt with ``dict(entry)``
    and the entry list with ``list(...)``. ``_advance`` then diffs the old snapshot
    against the new one, so a builder that mutated in place would corrupt that delta
    rather than merely leak state. Keep the discipline when adding one.
    """

    def __init__(self, projection: ProjectionStateV1) -> None:
        self._projection = projection.model_copy(
            update={"snapshot": with_session_preview(projection.snapshot)}
        )
        # The snapshot the journal holds. It trails ``_projection.snapshot`` by
        # whatever provisional content is live, so deltas are diffed against
        # what the store actually has rather than against the on-screen view.
        self._durable = self._projection.snapshot

    @property
    def projection(self) -> ProjectionStateV1:
        return self._projection

    def rebased(self, projection: ProjectionStateV1) -> "SessionProjector":
        """A projector on ``projection`` that keeps this one's live streamed content.

        Callers that resync to the store mid-turn would otherwise drop the
        provisional entries, which the journal deliberately does not hold: the
        answer on screen would blank out and the next fragment would restart it
        from that fragment alone. Use this wherever the resync is routine
        bookkeeping rather than a decision to abandon the live view.
        """
        rebased = SessionProjector(projection)
        live = self._projection.snapshot.history.entries
        if not any(_is_provisional(entry) for entry in live):
            return rebased
        # ``_durable`` stays the store's snapshot; only the live view gains the
        # entries back.
        stored = rebased._projection.snapshot
        entries = _restore_provisional_entries(stored.history.entries, live)
        rebased._projection = rebased._projection.model_copy(
            update={
                "snapshot": stored.model_copy(
                    update={"history": stored.history.model_copy(update={"entries": entries})}
                )
            }
        )
        return rebased

    def apply_action_started(
        self,
        action: RustToolCallAction | RustLLMCallAction,
        *,
        observed_at: int,
    ) -> ProjectionUpdate:
        state = self._projection.snapshot
        entries = list(state.history.entries)
        if isinstance(action, RustLLMCallAction):
            if action.purpose != "compaction":
                raise ValueError("only compaction LLM actions have public start entries")
            entry = _running_compaction_entry(state.session.id, entries, action, observed_at)
        else:
            entry = _running_effect_entry(state.session.id, action, observed_at)
        if not _replace_entry(entries, cast(str, entry["id"]), entry):
            entries.append(entry)
        public = state.model_copy(
            update={
                "session": state.session.model_copy(update={"updated_at": observed_at}),
                "history": state.history.model_copy(update={"entries": entries}),
            }
        )
        return self._advance(public)

    def apply(self, transition: RustSessionTransition, *, observed_at: int) -> ProjectionUpdate:
        state = self._projection.snapshot
        entries = list(state.history.entries)
        latest_turn = state.latest_turn
        token_usage = state.session.token_usage
        context_usage = state.session.context_usage
        preview = state.session.preview or first_user_message_preview(entries)
        for observation in transition.observations:
            if isinstance(observation, RustTurnStartedObservation):
                latest_turn = InProgressPublicTurn(
                    id=observation.turn_id,
                    session_id=state.session.id,
                    queue_item_id=_content_meta_value(observation.content, "vibe_queue_item_id"),
                    started_at=observed_at,
                )
                if content_is_injected(observation.content):
                    continue
                preview = preview or _content_preview(observation.content)
                entry = _message_entry(
                    state.session.id,
                    observation.turn_id,
                    f"user-{observation.turn_id}-{transition.input_id}",
                    "user",
                    observation.content,
                    observed_at,
                    "turn_start",
                )
                existing = next(
                    (
                        candidate
                        for candidate in entries
                        if candidate.get("type") == "message"
                        and candidate.get("role") == "user"
                        and candidate.get("turnId") == observation.turn_id
                        and candidate.get("source") == "turn_start"
                    ),
                    None,
                )
                if existing is None:
                    entries.append(entry)
            elif isinstance(observation, RustTurnSteeringReceivedObservation):
                entries.append(
                    _message_entry(
                        state.session.id,
                        observation.turn_id,
                        f"user-{observation.turn_id}-{transition.input_id}",
                        "user",
                        observation.content,
                        observed_at,
                        "turn_steer",
                    )
                )
            elif isinstance(observation, RustTurnSteeredObservation):
                pass
            elif isinstance(observation, RustAssistantMessageCommittedObservation):
                reasoning_parts = [
                    part
                    for part in observation.candidate.message.content
                    if isinstance(part, RustReasoningPart)
                ]
                for index, reasoning_part in enumerate(reasoning_parts):
                    entry = _reasoning_entry(
                        state.session.id,
                        observation.turn_id,
                        f"reasoning-{observation.action_id}-{index}",
                        reasoning_part,
                        observed_at,
                    )
                    if entry is not None and not _replace_entry(
                        entries, cast(str, entry["id"]), entry
                    ):
                        entries.append(entry)
                content = [
                    part
                    for part in observation.candidate.message.content
                    if getattr(part, "type", None)
                    in {"text", "image", "audio", "resource_link", "resource"}
                ]
                if content:
                    committed_entry = _message_entry(
                        state.session.id,
                        observation.turn_id,
                        f"assistant-{observation.action_id}",
                        "assistant",
                        content,
                        observed_at,
                        "harness",
                    )
                    if not _replace_entry(
                        entries, cast(str, committed_entry["id"]), committed_entry
                    ):
                        entries.append(committed_entry)
                # A provisional projection may hold entries the committed
                # candidate does not confirm (an empty final answer, reasoning
                # a hook dropped, or the tail of a superseded stream). Their
                # committed counterparts above are already settled, so any
                # remaining in-progress entry under this action is provisional
                # and must not outlive the commit. Settle rather than remove:
                # the client learns of history through added/updated events
                # only, so an entry dropped after it was published is never
                # retracted and spins in_progress for the rest of the session.
                entries = [
                    _settled_provisional_entry(entry, observed_at, cause="failure")
                    if _is_provisional(entry) and _belongs_to_action(entry, observation.action_id)
                    else entry
                    for entry in entries
                ]
                usage = observation.candidate.usage
                if usage is not None:
                    previous = token_usage or TokenUsage(
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        cached_input_tokens=0,
                    )
                    token_usage = TokenUsage(
                        input_tokens=previous.input_tokens + usage.input_tokens,
                        output_tokens=previous.output_tokens + usage.output_tokens,
                        total_tokens=previous.total_tokens + usage.total_tokens,
                        cached_input_tokens=(
                            previous.cached_input_tokens + usage.cached_input_tokens
                        ),
                    )
                    context_usage = TokenUsage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        total_tokens=usage.total_tokens,
                        cached_input_tokens=usage.cached_input_tokens,
                    )
            elif isinstance(observation, RustToolResultCommittedObservation):
                entry = _completed_effect_entry(state.session.id, entries, observation, observed_at)
                if not _replace_entry(entries, cast(str, entry["id"]), entry):
                    entries.append(entry)
            elif isinstance(observation, RustToolDiscoveryFinishedObservation):
                entry = _completed_tool_discovery_effect_entry(
                    state.session.id, observation, observed_at
                )
                if not _replace_entry(entries, cast(str, entry["id"]), entry):
                    entries.append(entry)
            elif isinstance(observation, RustContextCompactedObservation):
                entry = _completed_compaction_entry(
                    state.session.id, entries, observation, observed_at
                )
                if not _replace_entry(entries, cast(str, entry["id"]), entry):
                    entries.append(entry)
                if observation.usage is not None:
                    token_usage = _add_token_usage(token_usage, observation.usage)
                # Compaction is billed like any other call, but its prompt is the
                # context it replaced, not the one it produced. The summary that
                # survives is unmeasured until the next call reports.
                context_usage = TokenUsage(
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    cached_input_tokens=0,
                )
            elif isinstance(observation, RustContextCompactionFailedObservation):
                entry = _failed_compaction_entry(
                    state.session.id, entries, observation, observed_at
                )
                if not _replace_entry(entries, cast(str, entry["id"]), entry):
                    entries.append(entry)
            elif isinstance(observation, RustTurnCompletedObservation):
                started_at = _started_at(latest_turn, observation.turn_id, observed_at)
                # An open effect at a clean turn end never produced a result and
                # is dropped. Provisional assistant output is not: the user has
                # already watched it arrive, so it is settled in place. Every
                # committed candidate has superseded its own provisional entries
                # by now, so what survives here belongs to a completion the turn
                # abandoned -- retried under a fresh action id, say, while the
                # turn itself went on to finish cleanly. ``cause`` describes
                # that content's fate rather than the turn's, and "failure" is
                # the nearest of the three the lifecycle union allows.
                entries = [
                    entry for entry in entries if not _is_open_effect(entry, observation.turn_id)
                ]
                entries = [
                    _settled_provisional_entry(entry, observed_at, cause="failure")
                    if _is_provisional(entry) and entry.get("turnId") == observation.turn_id
                    else entry
                    for entry in entries
                ]
                latest_turn = CompletedPublicTurn(
                    id=observation.turn_id,
                    session_id=state.session.id,
                    queue_item_id=_turn_queue_item_id(latest_turn, observation.turn_id),
                    started_at=started_at,
                    completed_at=observed_at,
                )
            elif isinstance(observation, RustTurnInterruptedObservation):
                started_at = _started_at(latest_turn, observation.turn_id, observed_at)
                entries = _settle_open_effects(
                    entries,
                    observation.turn_id,
                    observed_at,
                    cancelled=True,
                    reason=observation.reason or "Turn interrupted",
                )
                latest_turn = InterruptedPublicTurn(
                    id=observation.turn_id,
                    session_id=state.session.id,
                    queue_item_id=_turn_queue_item_id(latest_turn, observation.turn_id),
                    started_at=started_at,
                    completed_at=observed_at,
                    reason=observation.reason,
                )
            elif isinstance(observation, RustTurnFailedObservation):
                started_at = _started_at(latest_turn, observation.turn_id, observed_at)
                entries = _settle_open_effects(
                    entries,
                    observation.turn_id,
                    observed_at,
                    cancelled=False,
                    reason=observation.error.message,
                )
                latest_turn = FailedPublicTurn(
                    id=observation.turn_id,
                    session_id=state.session.id,
                    queue_item_id=_turn_queue_item_id(latest_turn, observation.turn_id),
                    started_at=started_at,
                    completed_at=observed_at,
                    error=PublicError(
                        code=observation.error.code,
                        message=observation.error.message,
                        details=observation.error.details,
                    ),
                )

        turn = transition.turn
        if isinstance(turn, RustRunningTurn):
            status = RunningSessionStatus(active_turn_id=turn.turn_id)
        elif isinstance(turn, RustFailedTurn):
            status = FailedSessionStatus(message=turn.error.message)
        elif isinstance(turn, RustCompletedTurn | RustInterruptedTurn):
            status = IdleSessionStatus()
        else:
            status = IdleSessionStatus()
        public = state.model_copy(
            update={
                "session": state.session.model_copy(
                    update={
                        "status": status,
                        "updated_at": observed_at,
                        "preview": preview,
                        "token_usage": token_usage,
                        "context_usage": context_usage,
                    }
                ),
                "history": state.history.model_copy(update={"entries": entries}),
                "latest_turn": latest_turn,
            }
        )
        return self._advance(public)

    def append_public_history_entries(
        self, entries: list[JsonObject], *, observed_at: int
    ) -> ProjectionUpdate:
        state = self._projection.snapshot
        history = state.history.model_copy(update={"entries": [*state.history.entries, *entries]})
        public = state.model_copy(
            update={
                "session": state.session.model_copy(update={"updated_at": observed_at}),
                "history": history,
            }
        )
        return self._advance(public)

    def append_provisional_completion_content(
        self, delta: CompletionDelta, *, observed_at: int
    ) -> ProjectionUpdate:
        """Project one provisional fragment of an in-flight completion.

        Fragments upsert in-progress entries under the committed ids
        (``assistant-<action>`` / ``reasoning-<action>-0``), so the committed
        observation replaces them in place. ``restart`` first empties whatever
        the action had already projected, and may carry the replacement
        fragment in the same delta.

        The result is not journaled: see ``ProjectionUpdate.delta``.
        """
        state = self._projection.snapshot
        entries = list(state.history.entries)
        if delta.restart:
            entries = _cleared_provisional_entries(entries, delta.action_id, observed_at)
        if delta.reasoning:
            entries = _merge_provisional_reasoning(entries, state.session.id, delta, observed_at)
        if delta.text:
            entries = _merge_provisional_message(entries, state.session.id, delta, observed_at)
        history = state.history.model_copy(update={"entries": entries})
        public = state.model_copy(
            update={
                "session": state.session.model_copy(update={"updated_at": observed_at}),
                "history": history,
            }
        )
        return self._advance_provisional(public)

    def apply_model_change(self, model: str, *, observed_at: int) -> ProjectionUpdate:
        """Record that the session moved to a different model."""
        state = self._projection.snapshot
        entries = list(state.history.entries)
        entry = _model_change_entry(
            state.session.id, model, _model_change_count(entries) + 1, observed_at
        )
        entries.append(entry)
        public = state.model_copy(
            update={
                "session": state.session.model_copy(update={"updated_at": observed_at}),
                "history": state.history.model_copy(update={"entries": entries}),
            }
        )
        return self._advance(public)

    def rename_session(
        self, title: str, *, observed_at: int, source: TitleSource = "manual"
    ) -> ProjectionUpdate:
        return self._advance(
            renamed_session_state(
                self._projection.snapshot, title, observed_at=observed_at, source=source
            )
        )

    def _advance(self, public: PublicSessionState) -> ProjectionUpdate:
        durable = _without_provisional(public)
        watermark = self._projection.watermark + 1
        # The delta is derived from the last journaled snapshot and this
        # independently authored one; ``durable`` stays the oracle that the
        # delta is checked against, so a missed mutation surfaces as a replay
        # divergence rather than silently changing live behavior.
        delta = compute_projection_delta(self._durable, durable)
        self._durable = durable
        return self._published(public, watermark=watermark, delta=delta)

    def _advance_provisional(self, public: PublicSessionState) -> ProjectionUpdate:
        """Advance the live snapshot without journaling it.

        The watermark stands still because nothing was written; the session
        layer renumbers event ids, so successive provisional events still
        arrive in order.
        """
        return self._published(public, watermark=self._projection.watermark, delta=None)

    def _published(
        self,
        public: PublicSessionState,
        *,
        watermark: int,
        delta: ProjectionDelta | None,
    ) -> ProjectionUpdate:
        self._projection = ProjectionStateV1(
            session_id=self._projection.session_id,
            snapshot_sequence=self._projection.snapshot_sequence,
            watermark=watermark,
            snapshot=public,
        )
        return ProjectionUpdate(
            projection=self._projection,
            event=cast(
                JsonObject,
                {
                    "type": "session_state_updated",
                    "eventId": watermark,
                    "sessionId": public.session.id,
                    "state": public.model_dump(mode="json", by_alias=True),
                },
            ),
            delta=delta,
            journaled_snapshot=self._durable,
        )


def renamed_session_state(
    state: PublicSessionState, title: str, *, observed_at: int, source: TitleSource = "manual"
) -> PublicSessionState:
    return state.model_copy(
        update={
            "session": state.session.model_copy(
                update={"title": title, "title_source": source, "updated_at": observed_at}
            )
        }
    )


def public_message_entry(
    session_id: str,
    turn_id: str | None,
    entry_id: str,
    role: str,
    content: Sequence[Any],
    observed_at: int,
    source: str,
) -> JsonObject:
    return _message_entry(session_id, turn_id, entry_id, role, content, observed_at, source)


def content_is_injected(content: Sequence[Any]) -> bool:
    for part in content:
        meta = getattr(part, "meta", None)
        if isinstance(meta, dict) and meta.get("vibe.injected") is True:
            return True
    return False


def first_user_message_preview(entries: Sequence[JsonObject]) -> str:
    for entry in entries:
        if entry.get("type") != "message" or entry.get("role") != "user":
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            value = part.get("text")
            if isinstance(value, str):
                parts.append(value)
        text = _text_preview(parts)
        if text:
            return text
    return ""


def with_session_preview(state: PublicSessionState) -> PublicSessionState:
    if state.session.preview:
        return state
    preview = first_user_message_preview(state.history.entries)
    if not preview:
        return state
    return state.model_copy(
        update={"session": state.session.model_copy(update={"preview": preview})}
    )


def settle_stalled_projection(state: PublicSessionState, *, observed_at: int) -> PublicSessionState:
    """Present a mid-turn projection with no live owner as settled.

    Projects a ``running``/``blocked`` turn as ``interrupted``, settles its open
    effects, and marks the session idle, so clients never render a phantom
    "working" state. A pure presentation transform: the caller decides the
    session is actually unowned (e.g. via the lease), not this function.
    """
    status = state.session.status
    if not isinstance(status, (RunningSessionStatus, BlockedSessionStatus)):
        return state
    turn_id = status.active_turn_id
    reason = "Interrupted by process restart"
    entries = _settle_open_effects(
        list(state.history.entries),
        turn_id,
        observed_at,
        cancelled=True,
        reason=reason,
    )
    interrupted = InterruptedPublicTurn(
        id=turn_id,
        session_id=state.session.id,
        queue_item_id=_turn_queue_item_id(state.latest_turn, turn_id),
        started_at=_started_at(state.latest_turn, turn_id, observed_at),
        completed_at=observed_at,
        reason=reason,
    )
    return state.model_copy(
        update={
            "session": state.session.model_copy(update={"status": IdleSessionStatus()}),
            "history": state.history.model_copy(update={"entries": entries}),
            "latest_turn": interrupted,
        }
    )


def _reasoning_entry(
    session_id: str,
    turn_id: str | None,
    entry_id: str,
    part: RustReasoningPart,
    observed_at: int,
) -> JsonObject | None:
    """Build a public ``reasoning`` history entry from a ``RustReasoningPart``.

    Text content blocks are concatenated into ``text``; summary blocks are
    collected into ``summary``.  Redacted blocks have no user-visible text and
    are skipped unless they are the only content, in which case a placeholder
    keeps the entry present so the TUI can show that reasoning occurred.
    """
    text_parts: list[str] = []
    summary_parts: list[str] = []
    has_redacted = False
    for item in part.content:
        if isinstance(item, RustReasoningTextContent):
            text_parts.append(item.text)
        elif isinstance(item, RustReasoningSummaryContent):
            summary_parts.append(item.text)
        else:
            has_redacted = True
    text = "".join(text_parts)
    if not text and not summary_parts and not has_redacted:
        return None
    if not text and has_redacted:
        text = "[redacted]"
    return cast(
        JsonObject,
        {
            "type": "reasoning",
            "id": entry_id,
            "sessionId": session_id,
            "turnId": turn_id,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "outcome": {"type": "committed"},
            "relatedEntryId": None,
            "text": text,
            "summary": summary_parts,
        },
    )


def _message_entry(
    session_id: str,
    turn_id: str | None,
    entry_id: str,
    role: str,
    content: Sequence[Any],
    observed_at: int,
    source: str,
) -> JsonObject:
    client_message_id = _client_message_id(content) if role == "user" else None
    user_display_content = (
        _content_meta_object(content, "vibe.userDisplayContent") if role == "user" else None
    )
    return cast(
        JsonObject,
        {
            "type": "message",
            "id": client_message_id or entry_id,
            "sessionId": session_id,
            "turnId": turn_id,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "relatedEntryId": None,
            "role": role,
            "content": [_public_content_block(part) for part in content],
            "source": source,
            **(
                {"userDisplayContent": user_display_content}
                if user_display_content is not None
                else {}
            ),
            **({"outcome": {"type": "committed"}} if role == "assistant" else {}),
        },
    )


def _is_provisional(entry: JsonObject) -> bool:
    """Whether an entry is streamed completion content awaiting its commit.

    The committed builders (``_message_entry``, ``_reasoning_entry``) always
    author ``completed``, so an in-progress assistant message or reasoning
    entry can only have come from ``append_provisional_completion_content``.
    Both the per-action reset and the per-turn settle scope this same
    definition; keep them reading it rather than restating it.
    """
    return entry.get("generationStatus") == "in_progress" and (
        entry.get("type") == "reasoning"
        or (entry.get("type") == "message" and entry.get("role") == "assistant")
    )


def _restore_provisional_entries(
    stored: Sequence[JsonObject], live: Sequence[JsonObject]
) -> list[JsonObject]:
    """Re-insert ``live``'s provisional entries into ``stored`` where they sat.

    Each one goes back after the durable entry it followed in the live view.
    Appending them all at the tail instead would drop a streaming answer below
    whatever was journalled while it streamed -- a steering message, say --
    since the live view holds those after the provisional entry, not before.
    """
    trailing: dict[str | None, list[JsonObject]] = {}
    anchor: str | None = None
    for entry in live:
        if _is_provisional(entry):
            trailing.setdefault(anchor, []).append(entry)
            continue
        entry_id = entry.get("id")
        if isinstance(entry_id, str):
            anchor = entry_id
    restored = list(trailing.pop(None, []))
    for entry in stored:
        restored.append(entry)
        entry_id = entry.get("id")
        if isinstance(entry_id, str):
            restored.extend(trailing.pop(entry_id, []))
    # Anchored to something the store never wrote: keep the content, at the tail.
    for orphaned in trailing.values():
        restored.extend(orphaned)
    return restored


def _belongs_to_action(entry: JsonObject, action_id: str) -> bool:
    entry_id = entry.get("id")
    if not isinstance(entry_id, str):
        return False
    return entry_id == f"assistant-{action_id}" or entry_id.startswith(f"reasoning-{action_id}-")


def _merge_provisional_entry(
    entries: list[JsonObject],
    entry_id: str,
    append: JsonObject,
    extend: Callable[[JsonObject], JsonObject],
    observed_at: int,
) -> list[JsonObject]:
    for index, entry in enumerate(entries):
        if entry.get("id") != entry_id:
            continue
        if not _is_provisional(entry):
            # Committed or settled content supersedes a late fragment.
            return entries
        updated = extend(entry)
        updated["updatedAt"] = observed_at
        entries[index] = updated
        return entries
    entries.append(append)
    return entries


def _merge_provisional_message(
    entries: list[JsonObject],
    session_id: str,
    delta: CompletionDelta,
    observed_at: int,
) -> list[JsonObject]:
    entry_id = f"assistant-{delta.action_id}"

    def extend(entry: JsonObject) -> JsonObject:
        content = entry.get("content")
        blocks = list(content) if isinstance(content, list) else []
        head = blocks[0] if blocks and isinstance(blocks[0], dict) else {"type": "text", "text": ""}
        text = head.get("text")
        updated_head = {**head, "text": (text if isinstance(text, str) else "") + delta.text}
        return cast(JsonObject, {**entry, "content": [updated_head, *blocks[1:]]})

    return _merge_provisional_entry(
        entries,
        entry_id,
        cast(
            JsonObject,
            {
                "type": "message",
                "id": entry_id,
                "sessionId": session_id,
                "turnId": delta.turn_id,
                "createdAt": observed_at,
                "updatedAt": observed_at,
                "generationStatus": "in_progress",
                "outcome": None,
                "relatedEntryId": None,
                "role": "assistant",
                "content": [{"type": "text", "text": delta.text}],
                "source": "harness",
            },
        ),
        extend,
        observed_at,
    )


def _merge_provisional_reasoning(
    entries: list[JsonObject],
    session_id: str,
    delta: CompletionDelta,
    observed_at: int,
) -> list[JsonObject]:
    entry_id = f"reasoning-{delta.action_id}-0"

    def extend(entry: JsonObject) -> JsonObject:
        text = entry.get("text")
        return cast(
            JsonObject, {**entry, "text": (text if isinstance(text, str) else "") + delta.reasoning}
        )

    return _merge_provisional_entry(
        entries,
        entry_id,
        cast(
            JsonObject,
            {
                "type": "reasoning",
                "id": entry_id,
                "sessionId": session_id,
                "turnId": delta.turn_id,
                "createdAt": observed_at,
                "updatedAt": observed_at,
                "generationStatus": "in_progress",
                "outcome": None,
                "relatedEntryId": None,
                "text": delta.reasoning,
                "summary": [],
            },
        ),
        extend,
        observed_at,
    )


def _cleared_provisional_entries(
    entries: list[JsonObject], action_id: str, observed_at: int
) -> list[JsonObject]:
    """Blank the action's streamed content, keeping the entries themselves.

    A retry restates its answer from the beginning under the same ids, so the
    abandoned attempt's text has to go or the two would read as one. Removing
    the entries would do that too, but the client learns of history through
    added/updated events only: one dropped after it was published is never
    retracted and spins in_progress for the rest of the session. Emptied in
    place it keeps an id the retry can refill and the turn's end can settle.
    """
    cleared: list[JsonObject] = []
    for entry in entries:
        if not (_is_provisional(entry) and _belongs_to_action(entry, action_id)):
            cleared.append(entry)
            continue
        emptied = (
            {"text": ""}
            if entry.get("type") == "reasoning"
            else {"content": [{"type": "text", "text": ""}]}
        )
        cleared.append(cast(JsonObject, {**entry, **emptied, "updatedAt": observed_at}))
    return cleared


def _without_provisional(state: PublicSessionState) -> PublicSessionState:
    """The journalable view of ``state``: everything but live streamed content."""
    entries = state.history.entries
    durable = [entry for entry in entries if not _is_provisional(entry)]
    if len(durable) == len(entries):
        return state
    return state.model_copy(
        update={"history": state.history.model_copy(update={"entries": durable})}
    )


def public_notice_entry(
    session_id: str,
    entry_id: str,
    *,
    kind: str,
    scope: str,
    observed_at: int,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    hook_name: str | None = None,
    status: str | None = None,
    content: str | None = None,
) -> JsonObject:
    """Build a public ``notice`` history entry (``PublicNoticeEntry`` + ``HookNoticeDetail``
    wire shape) for a user hook. Unset detail fields are omitted; the client model forbids
    extra keys and defaults them.
    """
    detail: dict[str, JsonValue] = {"kind": kind, "scope": scope}
    if tool_name is not None:
        detail["toolName"] = tool_name
    if tool_call_id is not None:
        detail["toolCallId"] = tool_call_id
    if hook_name is not None:
        detail["hookName"] = hook_name
    if status is not None:
        detail["status"] = status
    if content is not None:
        detail["content"] = content
    level = "warning" if status == "warning" else "error" if status == "error" else "info"
    return cast(
        JsonObject,
        {
            "type": "notice",
            "id": entry_id,
            "sessionId": session_id,
            "turnId": None,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "level": level,
            "message": content or _notice_message(kind, hook_name),
            "detail": detail,
        },
    )


def _notice_message(kind: str, hook_name: str | None) -> str:
    if kind == "hook_run_started":
        return "Running hooks"
    if kind == "hook_run_completed":
        return "Hooks completed"
    if kind == "hook_started":
        return f"Running hook {hook_name}" if hook_name else "Running hook"
    return f"Hook {hook_name} completed" if hook_name else "Hook completed"


def _client_message_id(content: Sequence[Any]) -> str | None:
    return _content_meta_value(content, "vibe_client_message_id")


def _content_meta_value(content: Sequence[Any], key: str) -> str | None:
    for part in content:
        meta = getattr(part, "meta", None)
        if isinstance(meta, dict) and isinstance(meta.get(key), str):
            return meta[key]
    return None


def _content_meta_object(content: Sequence[Any], key: str) -> JsonObject | None:
    for part in content:
        meta = getattr(part, "meta", None)
        if isinstance(meta, dict) and isinstance(value := meta.get(key), dict):
            return cast(JsonObject, value)
    return None


def _content_preview(content: Sequence[Any]) -> str:
    return _text_preview([part.text for part in content if isinstance(part, RustTextContentBlock)])


def _text_preview(parts: Sequence[str]) -> str:
    text = "\n".join(parts).strip().replace("\n", " ")
    if len(text) > _PREVIEW_MAX_CHARS:
        return text[:_PREVIEW_MAX_CHARS].rstrip() + "…"
    return text


def _public_content_block(part: Any) -> JsonValue:
    serialized = part.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(part, RustImageContentBlock):
        return cast(
            JsonValue,
            {
                "type": "image",
                "attachment": {
                    "source": {"kind": "inline", "data": part.data},
                    "alias": "image",
                    "mimeType": part.mime_type,
                },
            },
        )
    if isinstance(serialized, dict) and serialized.get("type") == "resource_link":
        return cast(
            JsonValue,
            {
                "type": "resource",
                "resource": {
                    "kind": "link",
                    "uri": serialized["uri"],
                    **({"name": serialized["name"]} if serialized.get("name") else {}),
                    **({"title": serialized["title"]} if serialized.get("title") else {}),
                    **(
                        {"description": serialized["description"]}
                        if serialized.get("description")
                        else {}
                    ),
                    **({"mediaType": serialized["mimeType"]} if serialized.get("mimeType") else {}),
                    **({"size": serialized["size"]} if serialized.get("size") is not None else {}),
                },
            },
        )
    if isinstance(serialized, dict) and serialized.get("type") == "resource":
        resource = serialized.get("resource")
        if isinstance(resource, dict):
            return cast(
                JsonValue,
                {
                    "type": "resource",
                    "resource": {
                        "kind": "text" if "text" in resource else "blob",
                        "uri": resource["uri"],
                        **({"mediaType": resource["mimeType"]} if resource.get("mimeType") else {}),
                        **({"text": resource["text"]} if "text" in resource else {}),
                        **({"blob": resource["blob"]} if "blob" in resource else {}),
                    },
                },
            )
    if isinstance(serialized, dict):
        serialized.pop("_meta", None)
    return cast(JsonValue, serialized)


def _running_effect_entry(
    session_id: str, action: RustToolCallAction, observed_at: int
) -> JsonObject:
    tool_name, detail = _effect_detail(action)
    return cast(
        JsonObject,
        {
            "type": "effect",
            "id": f"effect-{action.action_id}",
            "sessionId": session_id,
            "turnId": action.turn_id,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "in_progress",
            "relatedEntryId": None,
            "title": tool_name,
            "detail": detail,
            "state": {"status": "running", "outputText": ""},
        },
    )


def _running_compaction_entry(
    session_id: str,
    entries: Sequence[JsonObject],
    action: RustLLMCallAction,
    observed_at: int,
) -> JsonObject:
    compaction_id = action.compaction_id
    attempt = action.attempt
    if compaction_id is None or attempt is None:
        raise ValueError("compaction action is missing its identity")
    entry_id = f"checkpoint-compaction-{compaction_id}"
    existing = _find_entry(entries, entry_id)
    created_at = existing.get("createdAt") if existing is not None else observed_at
    return cast(
        JsonObject,
        {
            "type": "checkpoint",
            "id": entry_id,
            "sessionId": session_id,
            "turnId": action.turn_id,
            "createdAt": created_at,
            "updatedAt": observed_at,
            "generationStatus": "in_progress",
            "relatedEntryId": None,
            "kind": "compaction",
            "message": "Compacting context",
            "details": {"trigger": action.trigger, "attempt": attempt},
        },
    )


def _model_change_count(entries: Sequence[JsonObject]) -> int:
    return sum(
        1
        for entry in entries
        if entry.get("type") == "checkpoint" and entry.get("kind") == "model_change"
    )


def _model_change_entry(session_id: str, model: str, sequence: int, observed_at: int) -> JsonObject:
    return cast(
        JsonObject,
        {
            "type": "checkpoint",
            "id": f"checkpoint-model-change-{sequence}",
            "sessionId": session_id,
            # The change sits between two turns, so it belongs to neither.
            "turnId": None,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "relatedEntryId": None,
            "kind": "model_change",
            "message": f"Model changed to {model}",
            "details": {"model": model},
        },
    )


def _completed_compaction_entry(
    session_id: str,
    entries: Sequence[JsonObject],
    observation: RustContextCompactedObservation,
    observed_at: int,
) -> JsonObject:
    return _terminal_compaction_entry(
        session_id,
        entries,
        compaction_id=observation.compaction_id,
        turn_id=observation.turn_id,
        observed_at=observed_at,
        message="Context compacted",
        details={
            "trigger": observation.trigger,
            "attempt": observation.attempt,
            "summaryLength": len(observation.summary),
        },
    )


def _compaction_failure_reason(
    error: RustProtocolError,
) -> Literal["tool_call", "empty_summary"] | None:
    """Map a Core summary rejection to its public compaction-failure reason.

    Core collapses every summary-content rejection into a single
    ``invalid_compaction_summary`` code, so the ``tool_call`` vs ``empty_summary``
    distinction is recovered from the Core-owned message. Other failure codes
    (size overflow, provider stream) have no summary-failure reason and return
    ``None``, so the Host emits ``auto_compact_triggered`` but not
    ``compaction_failed`` for them. This matches the Core error strings in
    ``core/src/core/features/compaction/execution.rs``.
    """
    if error.code != "invalid_compaction_summary":
        return None
    message = error.message.lower()
    if "tool call" in message:
        return "tool_call"
    if "empty" in message:
        return "empty_summary"
    # A malformed-delimiter summary produced no usable text either.
    return "empty_summary"


def _failed_compaction_entry(
    session_id: str,
    entries: Sequence[JsonObject],
    observation: RustContextCompactionFailedObservation,
    observed_at: int,
) -> JsonObject:
    return _terminal_compaction_entry(
        session_id,
        entries,
        compaction_id=observation.compaction_id,
        turn_id=observation.turn_id,
        observed_at=observed_at,
        message="Context compaction failed",
        details={
            "trigger": observation.trigger,
            "attempt": observation.attempt,
            "error": {
                "code": observation.error.code,
            },
            # Public summary-failure reason, recovered where the Core message is
            # available; None for size overflow or provider-stream failures.
            "reason": _compaction_failure_reason(observation.error),
        },
    )


def _terminal_compaction_entry(
    session_id: str,
    entries: Sequence[JsonObject],
    *,
    compaction_id: str,
    turn_id: str | None,
    observed_at: int,
    message: str,
    details: JsonObject,
) -> JsonObject:
    entry_id = f"checkpoint-compaction-{compaction_id}"
    existing = _find_entry(entries, entry_id)
    created_at = existing.get("createdAt") if existing is not None else observed_at
    return cast(
        JsonObject,
        {
            "type": "checkpoint",
            "id": entry_id,
            "sessionId": session_id,
            "turnId": turn_id,
            "createdAt": created_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "relatedEntryId": None,
            "kind": "compaction",
            "message": message,
            "details": details,
        },
    )


def _add_token_usage(current: TokenUsage | None, added: Any) -> TokenUsage:
    previous = current or TokenUsage(
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cached_input_tokens=0,
    )
    return TokenUsage(
        input_tokens=previous.input_tokens + added.input_tokens,
        output_tokens=previous.output_tokens + added.output_tokens,
        total_tokens=previous.total_tokens + added.total_tokens,
        cached_input_tokens=previous.cached_input_tokens + added.cached_input_tokens,
    )


def _completed_effect_entry(
    session_id: str,
    entries: Sequence[JsonObject],
    observation: RustToolResultCommittedObservation,
    observed_at: int,
) -> JsonObject:
    entry_id = f"effect-{observation.action_id}"
    existing = _find_entry(entries, entry_id)
    result = observation.result
    output_text = "\n".join(
        part.text for part in result.content if isinstance(part, RustTextContentBlock)
    )
    serialized = cast(
        JsonValue,
        result.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    approval = _approval_meta(result)
    if isinstance(result, RustToolFailureResult) and result.error.code == "tool_skipped":
        reason = output_text or result.error.message
        effect_state: JsonObject = {
            "status": "skipped",
            "reason": reason,
            "display": {"success": False, "message": reason},
        }
    elif isinstance(result, RustToolFailureResult):
        message = result.error.message
        effect_state = {
            "status": "failed",
            "error": {
                "code": result.error.code,
                "message": message,
                "details": result.error.details,
            },
            "output": serialized,
            "outputText": output_text,
            "display": {"success": False, "message": message},
        }
    else:
        title = _entry_title(existing) or "tool"
        message = _settled_message(existing) or f"{title} completed"
        output = serialized
        display: JsonObject = {"success": True, "message": message}
        note = _approval_note(result)
        if note is not None:
            display["approvalNote"] = note
        effect_state = {
            "status": "completed",
            "output": output,
            "outputText": output_text,
            "display": display,
        }
    if approval is not None:
        effect_state["decision"] = approval["decision"]
        effect_state["approvalType"] = approval["approvalType"]
        effect_state["approvalSource"] = approval["approvalSource"]
    if existing is not None:
        updated = dict(existing)
        detail = _completed_effect_detail(existing, result.meta)
        updated.update(
            {
                "updatedAt": observed_at,
                "generationStatus": "completed",
                "detail": detail,
                "state": effect_state,
            }
        )
        return cast(JsonObject, updated)
    add_uncorrelated_tool_result()
    tool_name = "tool"
    message = _effect_result_message(effect_state)
    return cast(
        JsonObject,
        {
            "type": "effect",
            "id": entry_id,
            "sessionId": session_id,
            "turnId": observation.turn_id,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "relatedEntryId": None,
            "title": tool_name,
            "detail": {
                "kind": "tool",
                "toolName": tool_name,
                "input": None,
                "display": {
                    "summary": tool_name,
                    "statusText": f"Running {tool_name}",
                    "settledMessage": message,
                },
            },
            "state": effect_state,
        },
    )


def _approval_note(result: object) -> str | None:
    if (
        not isinstance(result, (RustToolSuccessResult, RustToolFailureResult))
        or result.meta is None
    ):
        return None
    note = result.meta.get(APPROVAL_NOTE_META_KEY)
    return note if isinstance(note, str) and note else None


def _approval_meta(result: object) -> JsonObject | None:
    """Extract approval decision metadata from a tool result's ``_meta``."""
    if (
        not isinstance(result, (RustToolSuccessResult, RustToolFailureResult))
        or result.meta is None
    ):
        return None
    meta = result.meta.get(APPROVAL_META_KEY)
    if not isinstance(meta, dict):
        return None
    decision = meta.get("decision")
    approval_type = meta.get("approvalType")
    approval_source = meta.get("approvalSource")
    if not (
        isinstance(decision, str)
        and isinstance(approval_type, str)
        and isinstance(approval_source, str)
    ):
        return None
    return {
        "decision": decision,
        "approvalType": approval_type,
        "approvalSource": approval_source,
    }


def _completed_tool_discovery_effect_entry(
    session_id: str,
    observation: RustToolDiscoveryFinishedObservation,
    observed_at: int,
) -> JsonObject:
    """Make the Core-local discovery call visible without exposing its query."""

    message = "for relevant tools"
    return cast(
        JsonObject,
        {
            "type": "effect",
            "id": f"effect-tool-discovery-{observation.call_id}",
            "sessionId": session_id,
            "turnId": observation.turn_id,
            "createdAt": observed_at,
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "relatedEntryId": None,
            "title": "Searching for relevant tools",
            "detail": {
                "kind": "tool",
                "toolName": "search_tool_functions",
                "input": {"mode": observation.summary.kind},
                "display": {
                    "summary": "Searching for relevant tools",
                    "verb": "Searching",
                    "message": "Searching for relevant tools",
                    "settledVerb": "Searched",
                    "settledMessage": message,
                    "statusText": "Searching for relevant tools",
                },
            },
            "state": {
                "status": "completed",
                "output": {
                    "mode": observation.summary.kind,
                    "toolCount": observation.summary.tool_count,
                    "connectorNoticeCount": observation.summary.connector_notice_count,
                },
                "outputText": "",
                "display": {"success": True, "verb": "Searched", "message": message},
            },
        },
    )


def _settle_open_effects(
    entries: list[JsonObject],
    turn_id: str,
    observed_at: int,
    *,
    cancelled: bool,
    reason: str,
) -> list[JsonObject]:
    settled: list[JsonObject] = []
    for entry in entries:
        if _is_open_effect(entry, turn_id):
            settled.append(
                _settled_open_effect_entry(
                    entry,
                    observed_at,
                    cancelled=cancelled,
                    reason=reason,
                )
            )
        elif _is_provisional(entry) and entry.get("turnId") == turn_id:
            # Partial assistant text or reasoning from an abandoned or failed
            # completion stays visible with its content, matching the legacy
            # projector's finalize(): the turn will send no more of it. It is
            # discarded rather than committed -- the Core never accepted this
            # content, so it is not in the model's context and must not read
            # to the user as an answer the assistant stands behind.
            settled.append(
                _settled_provisional_entry(
                    entry, observed_at, cause="interrupt" if cancelled else "failure"
                )
            )
        else:
            settled.append(entry)
    return settled


def _is_open_effect(entry: JsonObject, turn_id: str) -> bool:
    return (
        entry.get("type") == "effect"
        and entry.get("turnId") == turn_id
        and entry.get("generationStatus") != "completed"
    )


def _settled_provisional_entry(
    entry: JsonObject, observed_at: int, *, cause: Literal["failure", "steer", "interrupt"]
) -> JsonObject:
    updated = dict(entry)
    updated.update(
        {
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "outcome": {"type": "discarded", "cause": cause},
        }
    )
    return cast(JsonObject, updated)


def _settled_open_effect_entry(
    entry: JsonObject,
    observed_at: int,
    *,
    cancelled: bool,
    reason: str,
) -> JsonObject:
    state = entry.get("state")
    output_text = ""
    if isinstance(state, dict):
        output = state.get("outputText")
        output_text = output if isinstance(output, str) else ""
    if cancelled:
        effect_state: JsonObject = {
            "status": "cancelled",
            "reason": reason,
            "outputText": output_text,
            "display": {"success": False, "message": reason},
        }
    else:
        effect_state = {
            "status": "failed",
            "error": {"message": reason},
            "outputText": output_text,
            "display": {"success": False, "message": reason},
        }
    updated = dict(entry)
    updated.update(
        {
            "updatedAt": observed_at,
            "generationStatus": "completed",
            "state": effect_state,
        }
    )
    return cast(JsonObject, updated)


def _effect_detail(action: RustToolCallAction) -> tuple[str, JsonObject]:
    if isinstance(action, RustRuntimeBuiltinToolCallAction):
        tool_name = action.call.name
        if tool_name == "subagent.spawn":
            arguments = action.call.arguments if isinstance(action.call.arguments, dict) else {}
            agent_name = _str_argument(cast(JsonObject, arguments), "agentName") or "subagent"
            agent_type = _str_argument(cast(JsonObject, arguments), "agentType") or "generic"
            message = _str_argument(cast(JsonObject, arguments), "message") or ""
            return (
                tool_name,
                {
                    "kind": "subagent",
                    "toolName": tool_name,
                    "input": {"task": message, "agent": agent_type},
                    "childSessionId": None,
                    "display": {
                        "summary": f"Starting {agent_name}",
                        "verb": "Starting",
                        "message": agent_name,
                        "settledVerb": "Started",
                        "settledMessage": f"Started {agent_name}",
                        "statusText": f"Starting {agent_name}",
                    },
                },
            )
    elif isinstance(action, RustProvidedToolCallAction):
        tool_name = f"{action.call.group_name}.{action.call.tool_name}"
    else:
        raise TypeError(f"Unsupported tool action: {type(action).__name__}")
    return (
        tool_name,
        {
            "kind": "tool",
            "toolName": tool_name,
            "input": action.call.arguments,
            "display": _generic_display(tool_name, tool_name),
        },
    )


def _generic_display(summary: str, tool_name: str) -> JsonObject:
    return {
        "summary": summary,
        "verb": "Running",
        "message": summary,
        "settledVerb": "Ran",
        "settledMessage": summary,
        "statusText": f"Running {tool_name}",
    }


def _str_argument(arguments: JsonObject, name: str) -> str | None:
    value = arguments.get(name)
    return value if isinstance(value, str) else None


def _completed_effect_detail(existing: JsonObject, result_meta: JsonObject | None) -> JsonObject:
    detail = existing.get("detail")
    if not isinstance(detail, dict) or detail.get("kind") != "subagent":
        return cast(JsonObject, detail) if isinstance(detail, dict) else {}
    annotation = result_meta.get("mistral.vibe.subagent") if result_meta is not None else None
    if not isinstance(annotation, dict):
        return cast(JsonObject, detail)
    child_session_id = annotation.get("childSessionId")
    if not isinstance(child_session_id, str) or not child_session_id:
        return cast(JsonObject, detail)
    completed = dict(detail)
    existing_child_session_id = completed.get("childSessionId")
    if existing_child_session_id not in {None, child_session_id}:
        raise ValueError("subagent effect child Session link changed after reservation")
    completed["childSessionId"] = child_session_id
    return cast(JsonObject, completed)


def _find_entry(entries: Sequence[JsonObject], entry_id: str) -> JsonObject | None:
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    return None


def _replace_entry(entries: list[JsonObject], entry_id: str, replacement: JsonObject) -> bool:
    for index, entry in enumerate(entries):
        if entry.get("id") == entry_id:
            entries[index] = replacement
            return True
    return False


def _entry_title(entry: JsonObject | None) -> str | None:
    if entry is None:
        return None
    title = entry.get("title")
    return title if isinstance(title, str) else None


def _settled_message(entry: JsonObject | None) -> str | None:
    if entry is None:
        return None
    detail = entry.get("detail")
    if not isinstance(detail, dict):
        return None
    display = detail.get("display")
    if not isinstance(display, dict):
        return None
    message = display.get("settledMessage")
    return message if isinstance(message, str) else None


def _effect_result_message(effect_state: JsonObject) -> str:
    display = effect_state.get("display")
    if not isinstance(display, dict):
        return "tool completed"
    message = display.get("message")
    return message if isinstance(message, str) else "tool completed"


def _started_at(latest_turn: object, turn_id: str, fallback: int) -> int:
    if isinstance(latest_turn, InProgressPublicTurn) and latest_turn.id == turn_id:
        return latest_turn.started_at
    return fallback


def _turn_queue_item_id(latest_turn: object, turn_id: str) -> str | None:
    if isinstance(latest_turn, InProgressPublicTurn) and latest_turn.id == turn_id:
        return latest_turn.queue_item_id
    return None


__all__ = ["ProjectionUpdate", "SessionProjector"]
