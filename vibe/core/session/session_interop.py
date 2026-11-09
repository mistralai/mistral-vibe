from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import (
    MESSAGES_FILENAME,
    METADATA_FILENAME,
    SessionLoader,
)
from vibe.core.types import (
    FileImageSource,
    ImageAttachment,
    InlineImageSource,
    LLMMessage,
    Role,
)
from vibe.core.utils import CANCELLATION_TAG, TOOL_ERROR_TAG, TaggedText
from vibe.user_content import UserBlobResource, UserResourceLink, UserTextResource
from vibe.utils.session_id import shorten_session_id

_REASONING_PAYLOADS_META_KEY = "vibe_reasoning_payloads"


class InvalidLegacyInteropSourceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LegacySessionReference:
    session_id: str
    cwd: str
    root_session_id: str
    parent_session_id: str | None


@dataclass(frozen=True, slots=True)
class LegacyCommittedHistory:
    reference: LegacySessionReference
    store_revision: str
    history: list[dict[str, JsonValue]]
    active_model: str | None = None

    @property
    def session_id(self) -> str:
        return self.reference.session_id


def resolve_legacy_session_reference(
    session_id: str, config: SessionLoggingConfig
) -> LegacySessionReference | None:
    sessions = SessionLoader.list_sessions(config)
    exact = [session for session in sessions if session["session_id"] == session_id]
    matches = exact or [
        session
        for session in sessions
        if shorten_session_id(session["session_id"]) == session_id
    ]
    if not matches:
        return None
    canonical_ids = {session["session_id"] for session in matches}
    if len(canonical_ids) != 1:
        raise InvalidLegacyInteropSourceError(
            f"Legacy session ID is ambiguous: {session_id}"
        )
    selected = matches[0]
    parents = {
        session["session_id"]: session["parent_session_id"] for session in sessions
    }
    canonical_id = selected["session_id"]
    return LegacySessionReference(
        session_id=canonical_id,
        cwd=selected["cwd"],
        root_session_id=_root_session_id(canonical_id, parents),
        parent_session_id=selected["parent_session_id"],
    )


def export_legacy_committed_history(
    session_id: str, config: SessionLoggingConfig
) -> LegacyCommittedHistory | None:
    """Export the normalized history accepted by the legacy resume path.

    ``SessionLoader.load_session`` remains the only parser for legacy message
    files. The extra candidate check only distinguishes an absent source from
    one whose legacy files exist but cannot be resumed safely.
    """
    reference = resolve_legacy_session_reference(session_id, config)
    session_dir = (
        SessionLoader.find_session_by_id(reference.session_id, config)
        if reference is not None
        else None
    )
    if reference is None or session_dir is None:
        candidates = _legacy_candidates(session_id, config)
        if candidates:
            raise InvalidLegacyInteropSourceError(
                f"Legacy session files cannot be loaded: {session_id}"
            )
        return None
    if session_dir.is_symlink():
        raise InvalidLegacyInteropSourceError(
            f"Legacy session directory cannot be a symbolic link: {session_id}"
        )
    metadata_path = session_dir / METADATA_FILENAME
    messages_path = session_dir / MESSAGES_FILENAME
    if metadata_path.is_symlink() or messages_path.is_symlink():
        raise InvalidLegacyInteropSourceError(
            f"Legacy session records cannot be symbolic links: {session_id}"
        )
    try:
        messages, metadata = SessionLoader.load_session(session_dir)
        canonical_id = metadata.get("session_id")
        if not isinstance(canonical_id, str) or canonical_id != reference.session_id:
            raise InvalidLegacyInteropSourceError(
                f"Legacy session identity changed while exporting: {session_id}"
            )
        revision = _legacy_store_revision(metadata_path, messages_path)
        history = export_messages_to_interop_history(messages, source_root=session_dir)
    except InvalidLegacyInteropSourceError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise InvalidLegacyInteropSourceError(
            f"Legacy session history is invalid: {session_id}: {exc}"
        ) from exc
    return LegacyCommittedHistory(
        reference=reference,
        store_revision=revision,
        history=history,
        active_model=_active_model_from_metadata(metadata),
    )


def export_messages_to_interop_history(
    messages: list[LLMMessage], *, source_root: Path
) -> list[dict[str, JsonValue]]:
    """Convert Vibe messages into the committed-history format Unified accepts."""
    return [_export_message(message, source_root) for message in messages]


def _active_model_from_metadata(metadata: dict[str, Any]) -> str | None:
    config = metadata.get("config")
    if not isinstance(config, dict):
        return None
    active_model = config.get("active_model")
    return active_model if isinstance(active_model, str) and active_model else None


def _legacy_candidates(session_id: str, config: SessionLoggingConfig) -> list[Path]:
    save_dir = Path(config.save_dir)
    if not save_dir.exists():
        return []
    return list(
        save_dir.glob(f"{config.session_prefix}_*_{shorten_session_id(session_id)}")
    )


def _legacy_store_revision(metadata_path: Path, messages_path: Path) -> str:
    digest = hashlib.sha256()
    for label, path in ((b"metadata", metadata_path), (b"messages", messages_path)):
        digest.update(label)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _export_message(message: LLMMessage, session_dir: Path) -> dict[str, JsonValue]:
    match message.role:
        case Role.system:
            return cast(
                dict[str, JsonValue],
                {
                    "role": "system",
                    "content": [{"type": "text", "text": message.content or ""}],
                },
            )
        case Role.user:
            return cast(
                dict[str, JsonValue],
                {"role": "user", "content": _export_user_content(message, session_dir)},
            )
        case Role.assistant:
            return cast(
                dict[str, JsonValue],
                {
                    "role": "assistant",
                    "parts": _export_assistant_parts(message, session_dir),
                },
            )
        case Role.tool:
            result = message.tool_result
            text = TaggedText.from_string(message.content or "")
            return cast(
                dict[str, JsonValue],
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "name": message.name or "",
                    "outcome": (
                        "failure"
                        if text.tag in {CANCELLATION_TAG, TOOL_ERROR_TAG}
                        or (result is not None and result.cancelled)
                        else "success"
                    ),
                    "content": (
                        [{"type": "text", "text": message.content}]
                        if message.content is not None
                        else []
                    ),
                },
            )
    raise AssertionError(f"Unsupported legacy role: {message.role}")


def _root_session_id(session_id: str, parents: dict[str, str | None]) -> str:
    seen: set[str] = set()
    current = session_id
    while (parent := parents.get(current)) is not None and parent in parents:
        if parent in seen:
            return session_id
        seen.add(current)
        current = parent
    return current


def _export_user_content(
    message: LLMMessage, session_dir: Path
) -> list[dict[str, JsonValue]]:
    content: list[dict[str, JsonValue]] = [
        {"type": "text", "text": message.content or ""}
    ]
    for image in message.images or []:
        content.append(_export_image_content(image, session_dir))
    for resource in message.resources or []:
        content.append(_export_resource_content(resource))
    return content


def _export_assistant_parts(
    message: LLMMessage, session_dir: Path
) -> list[dict[str, JsonValue]]:
    parts: list[dict[str, JsonValue]] = []
    if message.reasoning_content or message.reasoning_payloads:
        reasoning_part: dict[str, JsonValue] = {
            "type": "reasoning",
            "content": (
                [{"type": "text", "text": message.reasoning_content}]
                if message.reasoning_content
                else [{"type": "redacted", "data": ""}]
            ),
        }
        if message.reasoning_payloads:
            reasoning_part["_meta"] = cast(
                JsonValue, {_REASONING_PAYLOADS_META_KEY: message.reasoning_payloads}
            )
        parts.append(reasoning_part)
    if message.content is not None:
        parts.append({
            "type": "content",
            "content": {"type": "text", "text": message.content},
        })
    for image in message.images or []:
        parts.append({
            "type": "content",
            "content": _export_image_content(image, session_dir),
        })
    for resource in message.resources or []:
        parts.append({"type": "content", "content": _export_resource_content(resource)})
    for tool_call in message.tool_calls or []:
        raw = tool_call.function.arguments or ""
        try:
            value = cast(JsonValue, json.loads(raw))
            arguments: dict[str, JsonValue] = {
                "type": "json",
                "raw": raw,
                "value": value,
            }
        except json.JSONDecodeError as exc:
            arguments = {"type": "invalid_json", "raw": raw, "error": str(exc)}
        parts.append({
            "type": "tool_call",
            "id": tool_call.id or "",
            "name": tool_call.function.name or "",
            "arguments": arguments,
        })
    return parts or [{"type": "content", "content": {"type": "text", "text": ""}}]


def _export_image_content(
    image: ImageAttachment, session_dir: Path
) -> dict[str, JsonValue]:
    source = image.source
    file_fallback: dict[str, JsonValue] | None = None
    if isinstance(source, InlineImageSource):
        data = source.data
    elif isinstance(source, FileImageSource):
        path = source.path
        if not path.is_absolute():
            path = session_dir / path
        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise InvalidLegacyInteropSourceError(
                f"Cannot read persisted image {image.alias!r}: {exc}"
            ) from exc
        file_fallback = {"type": "file", "name": image.alias}
    else:
        raise InvalidLegacyInteropSourceError("Unknown persisted image source")
    return {
        "type": "image",
        "data": data,
        "mimeType": image.mime_type,
        **({"file_fallback": file_fallback} if file_fallback is not None else {}),
    }


def _export_resource_content(resource: Any) -> dict[str, JsonValue]:
    if isinstance(resource, UserTextResource):
        return {
            "type": "resource",
            "resource": {
                "uri": resource.uri,
                "mimeType": resource.media_type,
                "text": resource.text,
            },
        }
    if isinstance(resource, UserBlobResource):
        return {
            "type": "resource",
            "resource": {
                "uri": resource.uri,
                "mimeType": resource.media_type,
                "blob": resource.blob,
            },
        }
    if isinstance(resource, UserResourceLink):
        return {
            "type": "resource_link",
            "uri": resource.uri,
            "name": resource.name or resource.uri,
            "title": resource.title,
            "description": resource.description,
            "mimeType": resource.media_type,
            "size": resource.size,
        }
    raise InvalidLegacyInteropSourceError("Unknown persisted resource")


__all__ = [
    "InvalidLegacyInteropSourceError",
    "LegacyCommittedHistory",
    "LegacySessionReference",
    "export_legacy_committed_history",
    "resolve_legacy_session_reference",
]
