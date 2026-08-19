from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import JsonValue

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import (
    MESSAGES_FILENAME,
    METADATA_FILENAME,
    SessionLoader,
)
from vibe.core.types import (
    FileImageSource,
    FunctionCall,
    ImageAttachment,
    InlineImageSource,
    LLMMessage,
    PersistedToolResult,
    Role,
    ToolCall,
)
from vibe.core.utils import CANCELLATION_TAG, TOOL_ERROR_TAG, TaggedText
from vibe.user_content import UserBlobResource, UserResourceLink, UserTextResource
from vibe.utils.session_id import shorten_session_id


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
        history = [_export_message(message, session_dir) for message in messages]
    except InvalidLegacyInteropSourceError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise InvalidLegacyInteropSourceError(
            f"Legacy session history is invalid: {session_id}: {exc}"
        ) from exc
    return LegacyCommittedHistory(
        reference=reference, store_revision=revision, history=history
    )


def import_unified_committed_history(
    document: dict[str, Any],
) -> tuple[list[LLMMessage], dict[str, JsonValue]]:
    """Validate and map a Unified CommittedHistoryV1 into legacy messages."""
    if set(document) != {"interop_version", "source", "history", "history_sha256"}:
        raise ValueError("Unified interop export has unexpected fields")
    if document.get("interop_version") != 1:
        raise ValueError("Unsupported Unified interop export version")
    source = document.get("source")
    history = document.get("history")
    fingerprint = document.get("history_sha256")
    if not isinstance(source, dict) or source.get("backend") != "unified":
        raise ValueError("Unified interop export has an invalid source")
    if not isinstance(history, list) or not isinstance(fingerprint, str):
        raise ValueError("Unified interop export has an invalid history")
    messages = [_import_message(_require_object(item)) for item in history]
    provenance = cast(
        dict[str, JsonValue],
        {
            "interop_version": 1,
            "source": cast(dict[str, JsonValue], source),
            "history_sha256": fingerprint,
        },
    )
    return messages, provenance


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
    if message.reasoning_content:
        parts.append({
            "type": "reasoning",
            "content": [{"type": "text", "text": message.reasoning_content}],
        })
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
    else:
        raise InvalidLegacyInteropSourceError("Unknown persisted image source")
    return {"type": "image", "data": data, "mimeType": image.mime_type}


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


def _import_message(message: dict[str, Any]) -> LLMMessage:
    role = message.get("role")
    if role in {"system", "user"}:
        _require_fields(message, {"role", "content"})
        text, images, resources = _import_content(message.get("content"))
        return LLMMessage(
            role=Role(role),
            content="\n".join(text),
            images=images or None,
            resources=resources or None,
        )
    if role == "assistant":
        return _import_assistant_message(message)
    if role == "tool":
        allowed = {"role", "tool_call_id", "name", "outcome", "content", "_meta"}
        if not set(message).issubset(allowed) or not {
            "role",
            "tool_call_id",
            "name",
            "outcome",
            "content",
        }.issubset(message):
            raise ValueError("Tool interop message has unexpected fields")
        text, _images, _resources = _import_content(message.get("content"))
        outcome = message.get("outcome")
        if outcome not in {"success", "failure"}:
            raise ValueError("Tool interop outcome is invalid")
        return LLMMessage(
            role=Role.tool,
            tool_call_id=_require_string(message.get("tool_call_id")),
            name=_require_string(message.get("name")),
            content="\n".join(text),
            tool_result=PersistedToolResult(
                output={
                    "interop_outcome": cast(Literal["success", "failure"], outcome)
                },
                cancelled=outcome == "failure",
            ),
        )
    raise ValueError(f"Unsupported interop message role: {role!r}")


def _import_assistant_message(message: dict[str, Any]) -> LLMMessage:
    _require_fields(message, {"role", "parts"})
    text: list[str] = []
    reasoning: list[str] = []
    tool_calls: list[ToolCall] = []
    images: list[ImageAttachment] = []
    resources: list[Any] = []
    for raw_part in _require_list(message.get("parts")):
        part = _require_object(raw_part)
        part_type = part.get("type")
        if part_type == "content":
            _require_fields(part, {"type", "content"})
            part_text, part_images, part_resources = _import_content([
                part.get("content")
            ])
            text.extend(part_text)
            images.extend(part_images)
            resources.extend(part_resources)
        elif part_type == "reasoning":
            for raw_reasoning in _require_list(part.get("content")):
                reasoning_part = _require_object(raw_reasoning)
                value = reasoning_part.get("text", reasoning_part.get("data", ""))
                reasoning.append(_require_string(value))
        elif part_type == "tool_call":
            arguments = _require_object(part.get("arguments"))
            raw = _require_string(arguments.get("raw"))
            tool_calls.append(
                ToolCall(
                    id=_require_string(part.get("id")),
                    function=FunctionCall(
                        name=_require_string(part.get("name")), arguments=raw
                    ),
                )
            )
        else:
            raise ValueError(f"Unsupported assistant interop part: {part_type!r}")
    return LLMMessage(
        role=Role.assistant,
        content="\n".join(text),
        reasoning_content="\n".join(reasoning) or None,
        tool_calls=tool_calls or None,
        images=images or None,
        resources=resources or None,
    )


def _import_content(value: Any) -> tuple[list[str], list[ImageAttachment], list[Any]]:
    text: list[str] = []
    images: list[ImageAttachment] = []
    resources: list[Any] = []
    for raw_block in _require_list(value):
        block = _require_object(raw_block)
        block_type = block.get("type")
        if block_type == "text":
            text.append(_require_string(block.get("text")))
        elif block_type == "image":
            images.append(_import_image(block))
        elif block_type == "resource":
            resource = _require_object(block.get("resource"))
            uri = _require_string(resource.get("uri"))
            media_type = resource.get("mimeType")
            if media_type is not None:
                media_type = _require_string(media_type)
            if "text" in resource:
                resources.append(
                    UserTextResource(
                        uri=uri,
                        media_type=media_type,
                        text=_require_string(resource.get("text")),
                    )
                )
            elif "blob" in resource:
                resources.append(
                    UserBlobResource(
                        uri=uri,
                        media_type=media_type,
                        blob=_require_string(resource.get("blob")),
                    )
                )
            else:
                raise ValueError("Embedded resource has no content")
        elif block_type == "resource_link":
            resources.append(
                UserResourceLink(
                    uri=_require_string(block.get("uri")),
                    name=_optional_string(block.get("name")),
                    title=_optional_string(block.get("title")),
                    description=_optional_string(block.get("description")),
                    media_type=_optional_string(block.get("mimeType")),
                    size=block.get("size"),
                )
            )
        elif block_type == "audio":
            text.append(json.dumps(block, ensure_ascii=False, sort_keys=True))
        else:
            raise ValueError(f"Unsupported interop content block: {block_type!r}")
    return text, images, resources


def _import_image(block: dict[str, Any]) -> ImageAttachment:
    return ImageAttachment(
        source=InlineImageSource(data=_require_string(block.get("data"))),
        alias="imported-image",
        mime_type=_require_string(block.get("mimeType")),
    )


def _require_fields(value: dict[str, Any], fields: set[str]) -> None:
    if set(value) != fields:
        raise ValueError("Interop message has unexpected fields")


def _require_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected an interop object")
    return value


def _require_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Expected an interop list")
    return value


def _require_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected an interop string")
    return value


def _optional_string(value: Any) -> str | None:
    return None if value is None else _require_string(value)


__all__ = [
    "InvalidLegacyInteropSourceError",
    "LegacyCommittedHistory",
    "LegacySessionReference",
    "export_legacy_committed_history",
    "import_unified_committed_history",
    "resolve_legacy_session_reference",
]
