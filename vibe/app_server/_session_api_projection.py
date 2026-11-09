"""Vibe's public history, kept in step with Session API history events.

A read gives a page of Session API entries; events then add, patch or
complete one entry at a time. This module projects each API entry into the
Vibe public entries it feeds, so an event recomputes only those entries
instead of diffing the whole state.

An API entry feeds Vibe entries through the runtime's own record format, which
``_raw_entry`` rebuilds from the entry and its ``vibe.*`` annotations: a tool
call and its result become one effect, and a user message that invokes skills
becomes a message plus one effect per skill.
"""

from __future__ import annotations

from bisect import insort
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CheckpointHistoryEntry,
    EmbeddedResourceContentBlock,
    HistoryEntry,
    HistoryEntryAdapter,
    HistoryEntryAddedEvent,
    HistoryEntryCompletedEvent,
    HistoryEntryPatchedEvent,
    ImageContentBlock,
    JsonPatchOperation as ApiJsonPatchOperation,
    MessageHistoryEntry,
    NoticeHistoryEntry,
    ReasoningHistoryEntry,
    ResourceLinkContentBlock,
    TextContentBlock,
    ToolCallHistoryEntry,
    ToolResultHistoryEntry,
    UnknownHistoryEntry,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject
from mistralai_vibe_local_harness.vibe._session_api_entries import CALLBACK_ENTRY_FIELDS
from vibe.app_server._patch import apply_json_patch
from vibe.app_server._unified_history_projection import project_raw_history_entry
from vibe.app_server.models import JsonPatchOperation, PublicHistoryEntry

VIBE_PREFIX = "vibe."

type HistoryEvent = (
    HistoryEntryAddedEvent | HistoryEntryPatchedEvent | HistoryEntryCompletedEvent
)


@dataclass(frozen=True, slots=True)
class EntryChange:
    """One public entry an API history event added or changed."""

    raw: JsonObject | None
    """The runtime-format record the entry came from, for per-entry telemetry."""


@dataclass(slots=True)
class _Slot:
    """The Vibe entries one API entry feeds, with the record they came from."""

    raw: JsonObject | None
    history: list[PublicHistoryEntry]


class ApiHistoryProjection:
    """Vibe public entries kept in step with Session API history events."""

    def __init__(self, session_id: str, display_roots: Sequence[Path]) -> None:
        self._session_id = session_id
        self._display_roots = tuple(display_roots)
        self._entries: dict[str, HistoryEntry] = {}
        # (API index, API entry ID) of every entry that feeds a slot, in order.
        self._order: list[tuple[int, str]] = []
        self._slots: dict[str, _Slot] = {}
        self._results: dict[str, ToolResultHistoryEntry] = {}
        self._calls: dict[str, str] = {}

    @property
    def raw_entries(self) -> list[JsonObject]:
        """The runtime-format records of the page, in order."""
        return [
            raw
            for _, entry_id in self._order
            if (raw := self._slots[entry_id].raw) is not None
        ]

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return [
            entry
            for _, entry_id in self._order
            for entry in self._slots[entry_id].history
        ]

    def reset(self, entries: Sequence[HistoryEntry]) -> list[PublicHistoryEntry]:
        """Replace everything from a read's page of API entries; return the public history."""
        self._entries = {}
        self._order = []
        self._slots = {}
        self._results = {}
        self._calls = {}
        ordered = sorted(entries, key=lambda entry: entry.index)
        for entry in ordered:
            self._entries[entry.id] = entry
            if isinstance(entry, ToolResultHistoryEntry):
                self._results[entry.tool_call_id] = entry
        for entry in ordered:
            if isinstance(entry, ToolResultHistoryEntry):
                continue
            if isinstance(entry, ToolCallHistoryEntry):
                self._calls[entry.tool_call_id] = entry.id
            self._order.append((entry.index, entry.id))
            self._slots[entry.id] = self._project(entry)
        return self.history

    def apply(self, payload: HistoryEvent) -> list[EntryChange]:
        """Apply one API history event; return the public entries it added or changed."""
        match payload:
            case HistoryEntryAddedEvent() | HistoryEntryCompletedEvent():
                entry = payload.entry
            case HistoryEntryPatchedEvent():
                known = self._entries.get(payload.entry_id)
                # The entry is outside the page this projection was reset from.
                if known is None:
                    return []
                entry = _patched(known, payload.patches)
        self._entries[entry.id] = entry
        if isinstance(entry, ToolResultHistoryEntry):
            self._results[entry.tool_call_id] = entry
            call_id = self._calls.get(entry.tool_call_id)
            return [] if call_id is None else self._reproject(self._entries[call_id])
        if isinstance(entry, ToolCallHistoryEntry):
            self._calls[entry.tool_call_id] = entry.id
        if entry.id not in self._slots:
            insort(self._order, (entry.index, entry.id))
        return self._reproject(entry)

    def set_display_roots(self, display_roots: Sequence[Path]) -> list[EntryChange]:
        """Re-project every entry for moved roots; return what changed."""
        roots = tuple(display_roots)
        if roots == self._display_roots:
            return []
        self._display_roots = roots
        changes: list[EntryChange] = []
        for _, entry_id in self._order:
            slot = self._slots[entry_id]
            changes.extend(
                self._replace(entry_id, _Slot(slot.raw, self._public(slot.raw)))
            )
        return changes

    def _reproject(self, entry: HistoryEntry) -> list[EntryChange]:
        return self._replace(entry.id, self._project(entry))

    def _project(self, entry: HistoryEntry) -> _Slot:
        result = None
        if isinstance(entry, ToolCallHistoryEntry):
            result = self._results.get(entry.tool_call_id)
            # The effect's display rides on the result, so a call alone has no
            # valid public entry yet.
            if result is None:
                return _Slot(None, [])
        raw = _raw_entry(entry, self._session_id, result)
        return _Slot(raw, self._public(raw))

    def _public(self, raw: JsonObject | None) -> list[PublicHistoryEntry]:
        return (
            [] if raw is None else project_raw_history_entry(raw, self._display_roots)
        )

    def _replace(self, entry_id: str, slot: _Slot) -> list[EntryChange]:
        previous = self._slots.get(entry_id)
        prior = {entry.id: entry for entry in previous.history} if previous else {}
        changes: list[EntryChange] = []
        history: list[PublicHistoryEntry] = []
        for entry in slot.history:
            old = prior.get(entry.id)
            if old is not None and old == entry:
                # Keep the old object so callers can skip it by identity.
                history.append(old)
                continue
            changes.append(EntryChange(slot.raw))
            history.append(entry)
        self._slots[entry_id] = _Slot(slot.raw, history)
        return changes


def _patched(
    entry: HistoryEntry, patches: Sequence[ApiJsonPatchOperation]
) -> HistoryEntry:
    return HistoryEntryAdapter.validate_python(
        apply_json_patch(
            entry.model_dump(mode="json", by_alias=True),
            [
                JsonPatchOperation.model_validate(
                    patch.model_dump(mode="json", by_alias=True)
                )
                for patch in patches
            ],
        )
    )


def _raw_entry(  # noqa: PLR0911 - one return per entry type
    entry: HistoryEntry, session_id: str, result: ToolResultHistoryEntry | None
) -> JsonObject | None:
    match entry:
        case ToolCallHistoryEntry():
            return _raw_effect(entry, session_id, result)
        case MessageHistoryEntry():
            return _raw_message(entry, session_id)
        case ReasoningHistoryEntry():
            return {
                **_base(entry, session_id),
                "type": "reasoning",
                "text": entry.text,
                "summary": list(entry.summary),
                **_vibe_fields(entry, ("outcome",)),
            }
        case CheckpointHistoryEntry():
            kind = entry.annotations.get(VIBE_PREFIX + "checkpointKind")
            return {
                **_base(entry, session_id),
                "type": "checkpoint",
                "kind": kind if isinstance(kind, str) else str(entry.checkpoint_kind),
                "message": entry.message,
                "details": entry.details,
            }
        case NoticeHistoryEntry():
            key = entry.annotations.get(VIBE_PREFIX + "noticeDetailsKey", "detail")
            return {
                **_base(entry, session_id),
                "type": "notice",
                "level": str(entry.level),
                "message": entry.message,
                str(key): entry.details,
            }
        case UnknownHistoryEntry(original_type="callback"):
            # A settled question kept in history: its fields came as they are.
            extra = entry.model_extra or {}
            raw = {key: extra.get(key) for key in CALLBACK_ENTRY_FIELDS}
            return {**_base(entry, session_id), "type": "callback", **raw}
    return None


def _raw_message(entry: MessageHistoryEntry, session_id: str) -> JsonObject:
    return {
        **_base(entry, session_id),
        "type": "message",
        "role": entry.role,
        "content": [_raw_block(block) for block in entry.content],
        **_vibe_fields(entry, ("source", "userDisplayContent", "outcome")),
    }


def _raw_effect(
    call: ToolCallHistoryEntry, session_id: str, result: ToolResultHistoryEntry | None
) -> JsonObject:
    entry_id = call.id.removesuffix(":call")
    display = result.annotations.get(VIBE_PREFIX + "effect") if result else None
    display = display if isinstance(display, dict) else {}
    detail = display.get("detail")
    detail = dict(detail) if isinstance(detail, dict) else {}
    detail.setdefault("kind", "tool")
    detail.setdefault("toolName", call.name)
    detail.setdefault("input", call.arguments)
    raw_state = display.get("state")
    state: JsonObject = (
        dict(raw_state) if isinstance(raw_state, dict) else {"status": "running"}
    )
    # Only a completed tool has an output field; while it runs, the API
    # result holds the streamed text, which the state keeps as outputText.
    if (
        result is not None
        and result.result is not None
        and state.get("status") == "completed"
    ):
        state["output"] = result.result
    # As in _base: only "generating" runs, so a status a newer host added reads
    # as completed.
    finished = result is not None and result.generation_status != "generating"
    related = display.get("relatedEntryId")
    input_entry = display.get("inputEntryId")
    return {
        "type": "effect",
        "id": entry_id,
        "sessionId": session_id,
        "turnId": call.turn_id,
        "createdAt": call.created_at,
        "updatedAt": result.updated_at if result is not None else call.updated_at,
        "generationStatus": "completed" if finished else "in_progress",
        "relatedEntryId": related if isinstance(related, str) else None,
        "inputEntryId": input_entry if isinstance(input_entry, str) else None,
        "title": display.get("title") or call.name,
        "detail": cast(Any, detail),
        "state": cast(Any, state),
    }


def _base(entry: HistoryEntry, session_id: str) -> JsonObject:
    related = entry.annotations.get(VIBE_PREFIX + "relatedEntryId")
    input_entry = entry.annotations.get(VIBE_PREFIX + "inputEntryId")
    return {
        "id": entry.id,
        "sessionId": session_id,
        "turnId": entry.turn_id,
        "createdAt": entry.created_at,
        "updatedAt": entry.updated_at,
        "generationStatus": (
            "in_progress" if entry.generation_status == "generating" else "completed"
        ),
        "relatedEntryId": related if isinstance(related, str) else None,
        "inputEntryId": input_entry if isinstance(input_entry, str) else None,
    }


def _vibe_fields(entry: HistoryEntry, keys: Sequence[str]) -> JsonObject:
    return {
        key: entry.annotations[VIBE_PREFIX + key]
        for key in keys
        if VIBE_PREFIX + key in entry.annotations
    }


def _raw_block(block: object) -> JsonObject:
    match block:
        case TextContentBlock():
            return {"type": "text", "text": block.text}
        case ImageContentBlock():
            if block.uri.startswith("data:"):
                header, _, data = block.uri.partition(",")
                media_type = block.media_type or header[5:].removesuffix(";base64")
                return {
                    "type": "image",
                    "attachment": {
                        "source": {"kind": "inline", "data": data},
                        "alias": "image",
                        "mimeType": media_type,
                    },
                }
            return {
                "type": "image",
                "attachment": {
                    "source": {"kind": "file", "path": block.uri},
                    "alias": "image",
                    "mimeType": block.media_type,
                },
            }
        case ResourceLinkContentBlock():
            return {
                "type": "resource",
                "resource": {
                    "kind": "link",
                    "uri": block.uri,
                    **({"name": block.name} if block.name else {}),
                    **({"title": block.title} if block.title else {}),
                    **({"description": block.description} if block.description else {}),
                    **({"mediaType": block.media_type} if block.media_type else {}),
                    **({"size": block.size} if block.size is not None else {}),
                },
            }
        case EmbeddedResourceContentBlock():
            return {
                "type": "resource",
                "resource": {
                    "kind": "text" if block.text is not None else "blob",
                    "uri": block.uri,
                    **({"mediaType": block.media_type} if block.media_type else {}),
                    **({"text": block.text} if block.text is not None else {}),
                    **({"blob": block.blob} if block.blob is not None else {}),
                },
            }
    return {"type": "text", "text": ""}


__all__ = ["ApiHistoryProjection", "EntryChange"]
