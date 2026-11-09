"""Claude session discovery, transcript reading, and Vibe translation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from vibe.core.imports._session import VibeSessionPayload
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


class ClaudeTranscriptError(ValueError):
    """A Claude transcript cannot be read safely."""


@dataclass(frozen=True, slots=True)
class ClaudeSessionSummary:
    """The lightweight identity and path of one Claude session."""

    id: UUID
    path: Path


class ClaudeMessage(BaseModel):
    """The message subset emitted in Claude transcript rows."""

    model_config = ConfigDict(extra="ignore")

    role: str
    content: JsonValue


class ClaudeTranscriptRecord(BaseModel):
    """One JSONL row, retaining unknown Claude row kinds for future support."""

    model_config = ConfigDict(extra="ignore")

    type: str
    cwd: str | None = None
    timestamp: datetime | None = None
    message: ClaudeMessage | None = None
    ai_title: str | None = Field(default=None, alias="aiTitle")


@dataclass(frozen=True, slots=True)
class ClaudeTranscript:
    id: UUID
    path: Path
    cwd: Path
    records: list[ClaudeTranscriptRecord]


class ClaudeSessionSource:
    """Discovers, reads, and translates Claude sessions."""

    def __init__(self, projects_path: Path) -> None:
        self._projects_path = projects_path

    def discover(self) -> list[ClaudeSessionSummary]:
        if not self._projects_path.is_dir():
            return []
        sessions: list[ClaudeSessionSummary] = []
        for project_path in sorted(self._projects_path.iterdir()):
            if not project_path.is_dir():
                continue
            for transcript_path in sorted(project_path.glob("*.jsonl")):
                session_id = _parse_session_id(transcript_path)
                if session_id is not None:
                    sessions.append(
                        ClaudeSessionSummary(id=session_id, path=transcript_path)
                    )
        return sessions

    def read(self, summary: ClaudeSessionSummary) -> ClaudeTranscript:
        return self.read_path(summary.path)

    @staticmethod
    def read_path(transcript_path: Path) -> ClaudeTranscript:
        try:
            session_id = UUID(transcript_path.stem)
        except ValueError as exc:
            raise ClaudeTranscriptError(
                f"Claude transcript has no UUID filename: {transcript_path}"
            ) from exc

        records: list[ClaudeTranscriptRecord] = []
        try:
            transcript = transcript_path.open(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ClaudeTranscriptError(
                f"Could not read Claude transcript: {transcript_path}"
            ) from exc
        try:
            with transcript:
                for line in transcript:
                    records.append(ClaudeTranscriptRecord.model_validate_json(line))
        except (UnicodeError, ValidationError) as exc:
            raise ClaudeTranscriptError(
                f"Invalid Claude row in {transcript_path}"
            ) from exc

        cwd = next((record.cwd for record in records if record.cwd), None)
        if cwd is None:
            raise ClaudeTranscriptError(
                f"Claude transcript has no working directory: {transcript_path}"
            )
        return ClaudeTranscript(
            id=session_id, path=transcript_path, cwd=Path(cwd), records=records
        )

    def translate(self, source: ClaudeTranscript) -> VibeSessionPayload:
        try:
            return _ClaudeSessionTranslator().translate(source)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ClaudeTranscriptError(
                f"Invalid Claude transcript content: {source.path}"
            ) from exc


class _ClaudeSessionTranslator:
    """Translates Claude conversation rows into Vibe's message model."""

    def __init__(self) -> None:
        self._tool_names: dict[str, str] = {}

    def translate(self, source: ClaudeTranscript) -> VibeSessionPayload:
        messages: list[LLMMessage] = []
        title: str | None = None
        timestamps = [record.timestamp for record in source.records if record.timestamp]
        for record in source.records:
            if record.type == "ai-title" and record.ai_title:
                title = record.ai_title
            messages.extend(self.translate_record(record))
        return VibeSessionPayload(
            cwd=source.cwd,
            messages=messages,
            title=title,
            started_at=min(timestamps) if timestamps else None,
            source_harness="claude",
            source_session_id=str(source.id),
            source_cwd=None,
        )

    def translate_record(self, record: ClaudeTranscriptRecord) -> list[LLMMessage]:
        if record.message is None:
            return []
        if record.type == "assistant" and record.message.role == "assistant":
            return self._translate_assistant(record.message.content)
        if record.type == "user" and record.message.role == "user":
            return self._translate_user(record.message.content)
        return []

    def _translate_assistant(self, content: JsonValue) -> list[LLMMessage]:
        if isinstance(content, str):
            if not content:
                return []
            return [LLMMessage(role=Role.assistant, content=content)]
        if not isinstance(content, list):
            return []

        text: list[str] = []
        reasoning_payloads: list[dict[str, JsonValue]] = []
        tool_calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                continue
            match block["type"]:
                case "text":
                    block_text = block.get("text")
                    if isinstance(block_text, str):
                        text.append(block_text)
                case "thinking" | "redacted_thinking":
                    reasoning_payloads.append(block)
                case "tool_use":
                    tool_id = block.get("id")
                    tool_name = block.get("name")
                    if not isinstance(tool_id, str) or not isinstance(tool_name, str):
                        continue
                    self._tool_names[tool_id] = tool_name
                    tool_calls.append(
                        ToolCall(
                            id=tool_id,
                            function=FunctionCall(
                                name=tool_name,
                                arguments=json.dumps(block.get("input", {})),
                            ),
                        )
                    )
        if not text and not reasoning_payloads and not tool_calls:
            return []
        return [
            LLMMessage(
                role=Role.assistant,
                content="\n".join(text),
                reasoning_payloads=reasoning_payloads or None,
                tool_calls=tool_calls or None,
            )
        ]

    def _translate_user(self, content: JsonValue) -> list[LLMMessage]:
        if isinstance(content, str):
            return [LLMMessage(role=Role.user, content=content)]
        if not isinstance(content, list):
            return []
        messages: list[LLMMessage] = []
        text: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                block_text = block.get("text")
                if isinstance(block_text, str):
                    text.append(block_text)
                continue
            if block.get("type") != "tool_result":
                continue
            if text:
                messages.append(LLMMessage(role=Role.user, content="\n".join(text)))
                text.clear()
            tool_call_id = block.get("tool_use_id")
            if not isinstance(tool_call_id, str):
                continue
            messages.append(
                LLMMessage(
                    role=Role.tool,
                    name=self._tool_names.get(tool_call_id),
                    tool_call_id=tool_call_id,
                    content=_text_content(block.get("content")),
                )
            )
        if text:
            messages.append(LLMMessage(role=Role.user, content="\n".join(text)))
        return messages


def _parse_session_id(transcript_path: Path) -> UUID | None:
    try:
        return UUID(transcript_path.stem)
    except ValueError:
        return None


def _text_content(content: JsonValue | None) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_blocks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                text_blocks.append(text)
        return "\n".join(text_blocks)
    return json.dumps(content) if content is not None else ""
