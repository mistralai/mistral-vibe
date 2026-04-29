"""Tests for field_meta and locations in ACP tool overrides."""

from __future__ import annotations

from pathlib import Path

from acp.schema import ToolCallProgress, ToolCallStart
from pydantic import BaseModel

from vibe.acp.tools.builtins.grep import Grep
from vibe.acp.tools.builtins.read_file import ReadFile
from vibe.acp.tools.builtins.skill import Skill
from vibe.acp.tools.builtins.task import Task
from vibe.acp.tools.builtins.web_fetch import WebFetch
from vibe.acp.tools.builtins.web_search import WebSearch
from vibe.acp.tools.builtins.write_file import WriteFile
from vibe.acp.tools.session_update import (
    tool_call_session_update,
    tool_result_session_update,
)
from vibe.core.tools.builtins.grep import GrepArgs, GrepResult
from vibe.core.tools.builtins.read_file import ReadFileArgs, ReadFileResult
from vibe.core.tools.builtins.skill import SkillArgs, SkillResult
from vibe.core.tools.builtins.task import TaskArgs, TaskResult
from vibe.core.tools.builtins.webfetch import WebFetchArgs, WebFetchResult
from vibe.core.tools.builtins.websearch import (
    WebSearchArgs,
    WebSearchResult,
    WebSearchSource,
)
from vibe.core.tools.builtins.write_file import WriteFileArgs, WriteFileResult
from vibe.core.types import ToolCallEvent, ToolResultEvent


def _call_event(
    tool_name: str, tool_class: type, args: BaseModel | None
) -> ToolCallEvent:
    return ToolCallEvent(
        tool_name=tool_name, tool_call_id="tc-1", tool_class=tool_class, args=args
    )


def _result_event(
    tool_name: str, tool_class: type, result: BaseModel, *, error: str | None = None
) -> ToolResultEvent:
    return ToolResultEvent(
        tool_name=tool_name,
        tool_call_id="tc-1",
        tool_class=tool_class,
        result=result,
        error=error,
    )


class TestGrepFieldMeta:
    def test_call_meta_contains_query_and_tool_name(self) -> None:
        event = _call_event("grep", Grep, GrepArgs(pattern="TODO", path="src"))
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.field_meta == {"tool_name": "grep", "query": "TODO"}
        assert update.kind == "search"

    def test_call_location_is_resolved_search_path(self) -> None:
        event = _call_event("grep", Grep, GrepArgs(pattern="TODO", path="src"))
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.locations is not None
        assert len(update.locations) == 1
        assert update.locations[0].path == str(Path("src").resolve())

    def test_result_locations_from_parsed_matches(self) -> None:
        result = GrepResult(
            matches="src/a.py:10:match\nsrc/b.py:20:other",
            match_count=2,
            was_truncated=False,
        )
        event = _result_event("grep", Grep, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.status == "completed"
        assert update.locations is not None
        assert len(update.locations) == 2
        assert update.locations[0].path.endswith("src/a.py")
        assert update.locations[0].line == 10
        assert update.locations[1].path.endswith("src/b.py")
        assert update.locations[1].line == 20

    def test_fallback_on_none_args(self) -> None:
        event = _call_event("grep", Grep, None)
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.kind == "search"
        assert update.field_meta == {"tool_name": "grep"}


class TestReadFileFieldMeta:
    def test_call_location_has_offset_and_limit(self) -> None:
        event = _call_event(
            "read_file", ReadFile, ReadFileArgs(path="/tmp/f.txt", offset=10, limit=50)
        )
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.locations is not None
        loc = update.locations[0]
        assert loc.field_meta == {"type": "file_range", "offset": 10, "limit": 50}
        assert update.field_meta == {"tool_name": "read_file"}

    def test_call_defaults_offset_zero_limit_none(self) -> None:
        event = _call_event("read_file", ReadFile, ReadFileArgs(path="/tmp/f.txt"))
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.locations is not None
        loc = update.locations[0]
        assert loc.field_meta == {"type": "file_range", "offset": 0, "limit": None}

    def test_result_location_has_offset_and_lines_read(self) -> None:
        result = ReadFileResult(
            path="/tmp/f.txt",
            content="line1\nline2\nline3\n",
            lines_read=3,
            was_truncated=False,
            offset=10,
        )
        event = _result_event("read_file", ReadFile, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.locations is not None
        loc = update.locations[0]
        assert loc.field_meta == {"type": "file_range", "offset": 10, "limit": 3}


class TestWebSearchFieldMeta:
    def test_call_meta_contains_query(self) -> None:
        event = _call_event(
            "web_search", WebSearch, WebSearchArgs(query="python async")
        )
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.field_meta == {"tool_name": "web_search", "query": "python async"}
        assert update.kind == "search"

    def test_result_locations_are_source_urls_with_titles(self) -> None:
        result = WebSearchResult(
            answer="found it",
            sources=[
                WebSearchSource(title="Docs", url="https://docs.python.org"),
                WebSearchSource(title="Blog", url="https://blog.example.com"),
            ],
        )
        event = _result_event("web_search", WebSearch, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.locations is not None
        assert len(update.locations) == 2
        assert update.locations[0].path == "https://docs.python.org"
        assert update.locations[0].field_meta == {"type": "url", "title": "Docs"}
        assert update.locations[1].path == "https://blog.example.com"
        assert update.locations[1].field_meta == {"type": "url", "title": "Blog"}


class TestWebFetchFieldMeta:
    def test_call_location_is_normalized_url(self) -> None:
        event = _call_event("web_fetch", WebFetch, WebFetchArgs(url="example.com/page"))
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.kind == "fetch"
        assert update.locations is not None
        assert update.locations[0].path == "https://example.com/page"

    def test_result_location_has_char_count_and_truncated(self) -> None:
        result = WebFetchResult(
            url="https://example.com",
            content="hello world",
            content_type="text/html",
            was_truncated=True,
        )
        event = _result_event("web_fetch", WebFetch, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.locations is not None
        loc = update.locations[0]
        assert loc.path == "https://example.com"
        assert loc.field_meta == {"type": "url", "char_count": 11, "truncated": True}


class TestSkillFieldMeta:
    def test_call_meta_contains_skill_name(self) -> None:
        event = _call_event("skill", Skill, SkillArgs(name="debug"))
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.field_meta == {"tool_name": "skill", "skill_name": "debug"}
        assert update.kind == "read"

    def test_result_has_skill_dir_location(self) -> None:
        result = SkillResult(
            name="debug", content="do things", skill_dir="/home/user/.vibe/skills/debug"
        )
        event = _result_event("skill", Skill, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.locations is not None
        assert len(update.locations) == 1
        assert update.locations[0].path == str(
            Path("/home/user/.vibe/skills/debug").resolve()
        )
        assert update.field_meta == {"tool_name": "skill", "skill_name": "debug"}

    def test_result_no_location_when_no_skill_dir(self) -> None:
        result = SkillResult(name="debug", content="do things", skill_dir=None)
        event = _result_event("skill", Skill, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.locations is None


class TestTaskFieldMeta:
    def test_call_meta_contains_agent_and_task(self) -> None:
        event = _call_event(
            "task", Task, TaskArgs(task="explore codebase", agent="explore")
        )
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.field_meta == {
            "tool_name": "task",
            "agent": "explore",
            "task": "explore codebase",
        }

    def test_result_meta_contains_turn_count_and_response(self) -> None:
        result = TaskResult(response="found 3 files", turns_used=5, completed=True)
        event = _result_event("task", Task, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.status == "completed"
        assert update.field_meta == {
            "tool_name": "task",
            "turn_count": 5,
            "response": "found 3 files",
        }

    def test_result_failed_when_not_completed(self) -> None:
        result = TaskResult(response="interrupted", turns_used=2, completed=False)
        event = _result_event("task", Task, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.status == "failed"


class TestWriteFileFieldMeta:
    def test_call_meta_contains_tool_name(self) -> None:
        event = _call_event(
            "write_file", WriteFile, WriteFileArgs(path="out.txt", content="hello")
        )
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.field_meta == {"tool_name": "write_file"}
        assert update.kind == "edit"

    def test_call_location_is_resolved_path(self) -> None:
        event = _call_event(
            "write_file", WriteFile, WriteFileArgs(path="out.txt", content="hello")
        )
        update = tool_call_session_update(event)

        assert isinstance(update, ToolCallStart)
        assert update.locations is not None
        assert update.locations[0].path == str(Path("out.txt").resolve())

    def test_result_location_is_resolved_path(self) -> None:
        result = WriteFileResult(
            path="out.txt", content="hello", bytes_written=5, file_existed=False
        )
        event = _result_event("write_file", WriteFile, result)
        update = tool_result_session_update(event)

        assert isinstance(update, ToolCallProgress)
        assert update.locations is not None
        assert update.locations[0].path == str(Path("out.txt").resolve())
