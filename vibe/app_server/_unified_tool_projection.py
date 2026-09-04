"""Project source-neutral Unified tool history into Vibe public effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from vibe.app_server.models import (
    ApprovalCallbackDetail,
    CompletedEffectState,
    EffectCallDisplay,
    EffectDetail,
    EffectResultDisplay,
    FileEditEffectBatchInput,
    FileEditEffectChange,
    FileEditEffectDetail,
    FileEditEffectOccurrence,
    FileEditEffectOutput,
    FileReadEffectDetail,
    FileReadEffectInput,
    FileReadEffectOutput,
    FileWriteEffectDetail,
    FileWriteEffectInput,
    FileWriteEffectOutput,
    GenericEffectDetail,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicHistoryEntry,
    ShellEffectDetail,
    ShellEffectInput,
    ShellEffectOutput,
    SkillEffectDetail,
    SkillEffectInput,
)

_SEARCH_REPLACE_ANNOTATION_KEY = "mistralai.vibe.sdk.search_replace"
type UnifiedToolCategory = Literal[
    "file_edit", "file_read", "file_write", "shell", "skill"
]


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _ReadArguments(_SourceModel):
    path: str = Field(min_length=1)
    offset: int = Field(default=0, ge=-1)
    limit: int | None = Field(default=FileReadEffectInput.DEFAULT_LIMIT, ge=1)


class _ReadResult(_SourceModel):
    path: str = Field(min_length=1)
    content: str
    offset: int = Field(ge=0)
    lines_read: int = Field(ge=0)
    was_truncated: bool = False


class _WriteArguments(_SourceModel):
    path: str = Field(min_length=1)
    content: str


class _WriteResult(_SourceModel):
    path: str = Field(min_length=1)


class _SearchReplaceChange(_SourceModel):
    old_str: str = Field(min_length=1)
    new_str: str
    replace_all: bool = False

    @model_validator(mode="after")
    def validate_change(self) -> Self:
        if self.old_str == self.new_str:
            raise ValueError("old_str and new_str must differ")
        return self


class _SearchReplaceArguments(_SourceModel):
    file_path: str = Field(min_length=1)
    content: list[_SearchReplaceChange] = Field(min_length=1)


class _SearchReplaceResult(_SourceModel):
    file: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class _SearchReplacePreview(_SourceModel):
    old_start_line: int = Field(ge=1)
    old_lines: list[str]
    new_lines: list[str]


class _SearchReplaceAnnotations(_SourceModel):
    blocks: list[_SearchReplacePreview] = Field(min_length=1)


class _ShellArguments(_SourceModel):
    command: str = Field(min_length=1)


class _SkillArguments(_SourceModel):
    name: str = Field(min_length=1)


class _ShellResult(_SourceModel):
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    was_truncated: bool = False


class _ToolResultEnvelope(_SourceModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    structured_content: dict[str, JsonValue]
    meta: dict[str, JsonValue] | None = Field(default=None, alias="_meta")


@dataclass(frozen=True, slots=True)
class _ProjectedCall:
    detail: EffectDetail
    project_result: Callable[[CompletedEffectState], CompletedEffectState]


def project_unified_history_entry(entry: PublicHistoryEntry) -> PublicHistoryEntry:
    """Project one migration history entry without mutating source history."""
    if isinstance(entry, PublicEffectEntry):
        return _project_effect(entry)
    if not isinstance(entry, PublicCallbackEntry):
        return entry
    detail = entry.detail
    if not isinstance(detail, ApprovalCallbackDetail):
        return entry
    projected = _project_call(detail.effect)
    if projected is None:
        return entry
    return entry.model_copy(
        update={"detail": detail.model_copy(update={"effect": projected.detail})}
    )


def unified_tool_category(entry: PublicHistoryEntry) -> UnifiedToolCategory | None:
    """Return a bounded category only for recognized generic source calls."""
    detail: EffectDetail
    if isinstance(entry, PublicEffectEntry):
        detail = entry.detail
    elif isinstance(entry, PublicCallbackEntry) and isinstance(
        entry.detail, ApprovalCallbackDetail
    ):
        detail = entry.detail.effect
    else:
        return None
    if not isinstance(detail, GenericEffectDetail):
        return None
    return _TOOL_CATEGORIES.get(detail.tool_name)


def _project_effect(entry: PublicEffectEntry) -> PublicEffectEntry:
    projected = _project_call(entry.detail)
    if projected is None:
        return entry
    state = entry.state
    if isinstance(state, CompletedEffectState):
        try:
            state = projected.project_result(state)
        except (TypeError, ValueError, ValidationError):
            return entry
    return entry.model_copy(update={"detail": projected.detail, "state": state})


def _project_call(detail: EffectDetail) -> _ProjectedCall | None:
    if not isinstance(detail, GenericEffectDetail):
        return None
    projector = _CALL_PROJECTORS.get(detail.tool_name)
    if projector is not None:
        try:
            return projector(detail)
        except (TypeError, ValueError, ValidationError):
            return None
    label = _TOOL_LABELS.get(detail.tool_name)
    if label is not None:
        return _project_labeled(detail, label)
    return None


def _project_read(detail: GenericEffectDetail) -> _ProjectedCall:
    arguments = _ReadArguments.model_validate(detail.input)
    requested_offset = _display_offset(arguments.offset)
    semantic = FileReadEffectDetail(
        tool_name=detail.tool_name,
        input=FileReadEffectInput(
            file_path=arguments.path, offset=requested_offset, limit=arguments.limit
        ),
        display=_file_display("Reading", "Read", arguments.path),
    )

    def project_result(state: CompletedEffectState) -> CompletedEffectState:
        envelope = _ToolResultEnvelope.model_validate(state.output)
        result = _ReadResult.model_validate(envelope.structured_content)
        output = FileReadEffectOutput(
            file_path=result.path,
            content=result.content,
            num_lines=result.lines_read,
            start_line=result.offset + 1,
            requested_offset=requested_offset,
            requested_limit=arguments.limit,
            was_truncated=result.was_truncated,
        )
        word = "line" if result.lines_read == 1 else "lines"
        display = EffectResultDisplay(
            success=True,
            verb="Read",
            message=f"{result.lines_read} {word} from {result.path}",
            suffix="(truncated)" if result.was_truncated else "",
        )
        return _completed_state(state, output, display)

    return _ProjectedCall(detail=semantic, project_result=project_result)


def _project_write(detail: GenericEffectDetail) -> _ProjectedCall:
    arguments = _WriteArguments.model_validate(detail.input)
    semantic = FileWriteEffectDetail(
        tool_name=detail.tool_name,
        input=FileWriteEffectInput(file_path=arguments.path, content=arguments.content),
        display=_file_display(
            "Writing", "Created", arguments.path, content=arguments.content
        ),
    )

    def project_result(state: CompletedEffectState) -> CompletedEffectState:
        envelope = _ToolResultEnvelope.model_validate(state.output)
        result = _WriteResult.model_validate(envelope.structured_content)
        output = FileWriteEffectOutput(file_path=result.path, content=arguments.content)
        display = EffectResultDisplay(success=True, verb="Created", message=result.path)
        return _completed_state(state, output, display)

    return _ProjectedCall(detail=semantic, project_result=project_result)


def _project_search_replace(detail: GenericEffectDetail) -> _ProjectedCall:
    arguments = _SearchReplaceArguments.model_validate(detail.input)
    semantic = FileEditEffectDetail(
        tool_name=detail.tool_name,
        input=FileEditEffectBatchInput(
            file_path=arguments.file_path,
            changes=[
                FileEditEffectChange(
                    old_string=change.old_str,
                    new_string=change.new_str,
                    replace_all=change.replace_all,
                )
                for change in arguments.content
            ],
        ),
        display=_file_display("Editing", "Edited", arguments.file_path),
    )

    def project_result(state: CompletedEffectState) -> CompletedEffectState:
        envelope = _ToolResultEnvelope.model_validate(state.output)
        result = _SearchReplaceResult.model_validate(envelope.structured_content)
        annotations = _search_replace_annotations(envelope)
        output = FileEditEffectOutput(
            file=result.file,
            occurrences=[
                FileEditEffectOccurrence(
                    start_line=block.old_start_line,
                    old_text="".join(block.old_lines),
                    new_text="".join(block.new_lines),
                )
                for block in annotations.blocks
            ],
        )
        display = EffectResultDisplay(
            success=True, verb="Edited", message=result.file, warnings=result.warnings
        )
        return _completed_state(state, output, display)

    return _ProjectedCall(detail=semantic, project_result=project_result)


def _project_shell(detail: GenericEffectDetail) -> _ProjectedCall:
    arguments = _ShellArguments.model_validate(detail.input)
    semantic = ShellEffectDetail(
        tool_name=detail.tool_name,
        input=ShellEffectInput(command=arguments.command),
        display=EffectCallDisplay(
            summary=f"bash: {arguments.command}",
            verb="Running",
            message=arguments.command,
            settled_verb="Ran",
            settled_message=arguments.command,
            status_text="Running command",
        ),
    )

    def project_result(state: CompletedEffectState) -> CompletedEffectState:
        envelope = _ToolResultEnvelope.model_validate(state.output)
        result = _ShellResult.model_validate(envelope.structured_content)
        output = ShellEffectOutput(
            stdout=result.stdout,
            stderr=result.stderr,
            output=result.output,
            truncated=result.was_truncated,
        )
        display = EffectResultDisplay(
            success=True, verb="Ran", message=arguments.command
        )
        return _completed_state(state, output, display, output_text=output.transcript)

    return _ProjectedCall(detail=semantic, project_result=project_result)


def _project_skill(detail: GenericEffectDetail) -> _ProjectedCall:
    arguments = _SkillArguments.model_validate(detail.input)
    semantic = SkillEffectDetail(
        tool_name="skill",
        input=SkillEffectInput(name=arguments.name),
        display=EffectCallDisplay(
            summary=f"Loading skill: {arguments.name}",
            verb="Loading",
            message=f"skill: {arguments.name}",
            settled_verb="Loaded",
            settled_message=f"skill: {arguments.name}",
            status_text="Loading skill",
        ),
    )

    def project_result(state: CompletedEffectState) -> CompletedEffectState:
        return state.model_copy(
            update={
                "output": None,
                "display": EffectResultDisplay(
                    success=True, verb="Loaded", message=f"skill: {arguments.name}"
                ),
            }
        )

    return _ProjectedCall(detail=semantic, project_result=project_result)


def _search_replace_annotations(
    envelope: _ToolResultEnvelope,
) -> _SearchReplaceAnnotations:
    if envelope.meta is None:
        raise ValueError("search_replace result has no annotations")
    return _SearchReplaceAnnotations.model_validate(
        envelope.meta.get(_SEARCH_REPLACE_ANNOTATION_KEY)
    )


def _completed_state(
    state: CompletedEffectState,
    output: BaseModel,
    display: EffectResultDisplay,
    *,
    output_text: str | None = None,
) -> CompletedEffectState:
    update: dict[str, object] = {
        "output": cast(
            JsonValue, output.model_dump(mode="json", by_alias=True, exclude_none=False)
        ),
        "display": display,
    }
    if output_text is not None:
        update["output_text"] = output_text
    return state.model_copy(update=update)


def _display_offset(offset: int) -> int | None:
    if offset == 0:
        return None
    return offset + 1 if offset > 0 else offset


def _file_display(
    active_verb: Literal["Editing", "Reading", "Writing"],
    settled_verb: Literal["Created", "Edited", "Read"],
    path: str,
    *,
    content: str | None = None,
) -> EffectCallDisplay:
    return EffectCallDisplay(
        summary=f"{active_verb} {path}",
        content=content,
        verb="Creating" if active_verb == "Writing" else active_verb,
        message=path,
        settled_verb=settled_verb,
        settled_message=path,
        status_text=f"{active_verb} file",
    )


@dataclass(frozen=True, slots=True)
class _ToolLabel:
    """A human-readable header for a Unified tool that carries no rich projection.

    The Harness reports these tools under their namespaced key (``ui``,
    ``self``, ``subagent``, ``process``, ``connector_*``); the raw key is a
    routing detail, not a label a person should read. We keep ``tool_name``
    untouched (identity and telemetry still key on it) and only rewrite the
    presentation ``display``, mirroring the wording the legacy tool
    presentations use.
    """

    verb: str
    noun: str
    settled_verb: str

    @property
    def _summary(self) -> str:
        return f"{self.verb} {self.noun}".strip()

    def call_display(self) -> EffectCallDisplay:
        # An empty noun means "verb only": keep the message as "" so the header
        # renders the verb alone. A None message makes the TUI fall back to the
        # summary, which would duplicate the verb ("Sleeping Sleeping").
        return EffectCallDisplay(
            summary=self._summary,
            verb=self.verb,
            message=self.noun,
            settled_verb=self.settled_verb,
            settled_message=self.noun,
            status_text=self._summary,
        )


# Namespaced builtins and connector tools the Harness runs without a semantic
# projection. Keyed by the exact Unified tool key; anything absent stays generic
# and unchanged so unknown connector/MCP tools keep their reported presentation.
#
# Connector tool keys follow the ``connector_<name>.<function>`` convention.
# Labels mirror the verb/noun pattern of the builtin entries above.
_TOOL_LABELS: dict[str, _ToolLabel] = {
    # ── Built-in harness tools ──────────────────────────────────────────
    "ui.ask_user_question": _ToolLabel("Asking", "a question", "Asked"),
    "self.sleep": _ToolLabel("Sleeping", "", "Slept"),
    "subagent.list": _ToolLabel("Listing", "subagents", "Listed"),
    "subagent.wait": _ToolLabel("Waiting", "for a subagent", "Waited"),
    "subagent.send_message": _ToolLabel("Sending", "a message to a subagent", "Sent"),
    "subagent.interrupt": _ToolLabel("Interrupting", "a subagent", "Interrupted"),
    "subagent.stop": _ToolLabel("Stopping", "a subagent", "Stopped"),
    "process.start": _ToolLabel("Starting", "a background process", "Started"),
    "process.stop": _ToolLabel("Stopping", "a background process", "Stopped"),
    "process.output": _ToolLabel("Reading", "process output", "Read"),
    "process.list": _ToolLabel("Listing", "background processes", "Listed"),
    "process.write": _ToolLabel("Writing", "to a background process", "Wrote"),
    # ── GitHub App connector ────────────────────────────────────────────
    "connector_github_app.add_comment_to_pending_review": _ToolLabel(
        "Adding", "a review comment", "Added"
    ),
    "connector_github_app.add_issue_comment": _ToolLabel(
        "Commenting", "on an issue", "Commented"
    ),
    "connector_github_app.add_reply_to_pull_request_comment": _ToolLabel(
        "Replying", "to a pull request comment", "Replied"
    ),
    "connector_github_app.create_branch": _ToolLabel("Creating", "a branch", "Created"),
    "connector_github_app.create_or_update_file": _ToolLabel(
        "Creating", "or updating a file", "Created"
    ),
    "connector_github_app.create_pull_request": _ToolLabel(
        "Creating", "a pull request", "Created"
    ),
    "connector_github_app.create_repository": _ToolLabel(
        "Creating", "a repository", "Created"
    ),
    "connector_github_app.delete_file": _ToolLabel("Deleting", "a file", "Deleted"),
    "connector_github_app.fork_repository": _ToolLabel(
        "Forking", "a repository", "Forked"
    ),
    "connector_github_app.get_commit": _ToolLabel("Reading", "a commit", "Read"),
    "connector_github_app.get_file_contents": _ToolLabel(
        "Reading", "file contents", "Read"
    ),
    "connector_github_app.get_label": _ToolLabel("Reading", "a label", "Read"),
    "connector_github_app.get_latest_release": _ToolLabel(
        "Reading", "the latest release", "Read"
    ),
    "connector_github_app.get_me": _ToolLabel("Reading", "GitHub profile", "Read"),
    "connector_github_app.get_release_by_tag": _ToolLabel(
        "Reading", "a release by tag", "Read"
    ),
    "connector_github_app.get_tag": _ToolLabel("Reading", "a tag", "Read"),
    "connector_github_app.get_team_members": _ToolLabel(
        "Reading", "team members", "Read"
    ),
    "connector_github_app.get_teams": _ToolLabel("Reading", "teams", "Read"),
    "connector_github_app.issue_read": _ToolLabel("Reading", "an issue", "Read"),
    "connector_github_app.issue_write": _ToolLabel("Writing", "an issue", "Wrote"),
    "connector_github_app.list_branches": _ToolLabel("Listing", "branches", "Listed"),
    "connector_github_app.list_commits": _ToolLabel("Listing", "commits", "Listed"),
    "connector_github_app.list_issue_fields": _ToolLabel(
        "Listing", "issue fields", "Listed"
    ),
    "connector_github_app.list_issue_types": _ToolLabel(
        "Listing", "issue types", "Listed"
    ),
    "connector_github_app.list_issues": _ToolLabel("Listing", "issues", "Listed"),
    "connector_github_app.list_pull_requests": _ToolLabel(
        "Listing", "pull requests", "Listed"
    ),
    "connector_github_app.list_releases": _ToolLabel("Listing", "releases", "Listed"),
    "connector_github_app.list_repository_collaborators": _ToolLabel(
        "Listing", "repository collaborators", "Listed"
    ),
    "connector_github_app.list_tags": _ToolLabel("Listing", "tags", "Listed"),
    "connector_github_app.merge_pull_request": _ToolLabel(
        "Merging", "a pull request", "Merged"
    ),
    "connector_github_app.pull_request_read": _ToolLabel(
        "Reading", "a pull request", "Read"
    ),
    "connector_github_app.pull_request_review_write": _ToolLabel(
        "Writing", "a pull request review", "Wrote"
    ),
    "connector_github_app.push_files": _ToolLabel("Pushing", "files", "Pushed"),
    "connector_github_app.request_copilot_review": _ToolLabel(
        "Requesting", "a Copilot review", "Requested"
    ),
    "connector_github_app.run_secret_scanning": _ToolLabel(
        "Scanning", "for secrets", "Scanned"
    ),
    "connector_github_app.search_code": _ToolLabel("Searching", "code", "Searched"),
    "connector_github_app.search_commits": _ToolLabel(
        "Searching", "commits", "Searched"
    ),
    "connector_github_app.search_issues": _ToolLabel("Searching", "issues", "Searched"),
    "connector_github_app.search_pull_requests": _ToolLabel(
        "Searching", "pull requests", "Searched"
    ),
    "connector_github_app.search_repositories": _ToolLabel(
        "Searching", "repositories", "Searched"
    ),
    "connector_github_app.search_users": _ToolLabel("Searching", "users", "Searched"),
    "connector_github_app.sub_issue_write": _ToolLabel(
        "Writing", "a sub-issue", "Wrote"
    ),
    "connector_github_app.update_pull_request": _ToolLabel(
        "Updating", "a pull request", "Updated"
    ),
    "connector_github_app.update_pull_request_branch": _ToolLabel(
        "Updating", "a pull request branch", "Updated"
    ),
    # ── Google Calendar connector ───────────────────────────────────────
    "connector_google_calendar.create_event": _ToolLabel(
        "Creating", "a calendar event", "Created"
    ),
    "connector_google_calendar.delete_event": _ToolLabel(
        "Deleting", "a calendar event", "Deleted"
    ),
    "connector_google_calendar.get_event": _ToolLabel(
        "Reading", "a calendar event", "Read"
    ),
    "connector_google_calendar.list_calendars": _ToolLabel(
        "Listing", "calendars", "Listed"
    ),
    "connector_google_calendar.list_events": _ToolLabel(
        "Listing", "calendar events", "Listed"
    ),
    "connector_google_calendar.respond_to_event": _ToolLabel(
        "Responding", "to a calendar event", "Responded"
    ),
    "connector_google_calendar.update_event": _ToolLabel(
        "Updating", "a calendar event", "Updated"
    ),
    # ── Google Drive connector ───────────────────────────────────────────
    "connector_google_drive_mcp.copy_file": _ToolLabel(
        "Copying", "a Drive file", "Copied"
    ),
    "connector_google_drive_mcp.create_file": _ToolLabel(
        "Creating", "a Drive file", "Created"
    ),
    "connector_google_drive_mcp.download_file_content": _ToolLabel(
        "Downloading", "a Drive file", "Downloaded"
    ),
    "connector_google_drive_mcp.get_file_metadata": _ToolLabel(
        "Reading", "Drive file metadata", "Read"
    ),
    "connector_google_drive_mcp.get_file_permissions": _ToolLabel(
        "Reading", "Drive file permissions", "Read"
    ),
    "connector_google_drive_mcp.list_recent_files": _ToolLabel(
        "Listing", "recent Drive files", "Listed"
    ),
    "connector_google_drive_mcp.read_file_content": _ToolLabel(
        "Reading", "a Drive file", "Read"
    ),
    "connector_google_drive_mcp.search_files": _ToolLabel(
        "Searching", "Drive files", "Searched"
    ),
    # ── Mistral AI connector ────────────────────────────────────────────
    "connector_mistral_ai.delete_skill": _ToolLabel("Deleting", "a skill", "Deleted"),
    "connector_mistral_ai.list_skills": _ToolLabel("Listing", "skills", "Listed"),
    "connector_mistral_ai.read_skill": _ToolLabel("Reading", "a skill", "Read"),
    "connector_mistral_ai.read_skill_asset": _ToolLabel(
        "Reading", "a skill asset", "Read"
    ),
    "connector_mistral_ai.search_workspace_members": _ToolLabel(
        "Searching", "workspace members", "Searched"
    ),
    "connector_mistral_ai.set_skill_sharing": _ToolLabel(
        "Setting", "skill sharing", "Set"
    ),
    "connector_mistral_ai.write_skill": _ToolLabel("Writing", "a skill", "Wrote"),
    # ── Slack connector ─────────────────────────────────────────────────
    "connector_slack.slack_add_reaction": _ToolLabel(
        "Adding", "a Slack reaction", "Added"
    ),
    "connector_slack.slack_create_canvas": _ToolLabel(
        "Creating", "a Slack canvas", "Created"
    ),
    "connector_slack.slack_create_conversation": _ToolLabel(
        "Creating", "a Slack conversation", "Created"
    ),
    "connector_slack.slack_get_reactions": _ToolLabel(
        "Reading", "Slack reactions", "Read"
    ),
    "connector_slack.slack_list_channel_members": _ToolLabel(
        "Listing", "Slack channel members", "Listed"
    ),
    "connector_slack.slack_read_canvas": _ToolLabel(
        "Reading", "a Slack canvas", "Read"
    ),
    "connector_slack.slack_read_channel": _ToolLabel(
        "Reading", "a Slack channel", "Read"
    ),
    "connector_slack.slack_read_file": _ToolLabel("Reading", "a Slack file", "Read"),
    "connector_slack.slack_read_thread": _ToolLabel(
        "Reading", "a Slack thread", "Read"
    ),
    "connector_slack.slack_read_user_profile": _ToolLabel(
        "Reading", "a Slack user profile", "Read"
    ),
    "connector_slack.slack_schedule_message": _ToolLabel(
        "Scheduling", "a Slack message", "Scheduled"
    ),
    "connector_slack.slack_search_channels": _ToolLabel(
        "Searching", "Slack channels", "Searched"
    ),
    "connector_slack.slack_search_emojis": _ToolLabel(
        "Searching", "Slack emojis", "Searched"
    ),
    "connector_slack.slack_search_public": _ToolLabel(
        "Searching", "public Slack messages", "Searched"
    ),
    "connector_slack.slack_search_public_and_private": _ToolLabel(
        "Searching", "Slack messages", "Searched"
    ),
    "connector_slack.slack_search_users": _ToolLabel(
        "Searching", "Slack users", "Searched"
    ),
    "connector_slack.slack_send_message": _ToolLabel(
        "Sending", "a Slack message", "Sent"
    ),
    "connector_slack.slack_send_message_draft": _ToolLabel(
        "Drafting", "a Slack message", "Drafted"
    ),
    "connector_slack.slack_update_canvas": _ToolLabel(
        "Updating", "a Slack canvas", "Updated"
    ),
    # ── Web Search connector ─────────────────────────────────────────────
    "connector_web_search.finance_search": _ToolLabel(
        "Searching", "financial data", "Searched"
    ),
    "connector_web_search.news_search": _ToolLabel("Searching", "news", "Searched"),
    "connector_web_search.open_url": _ToolLabel("Opening", "a URL", "Opened"),
    "connector_web_search.weather_search": _ToolLabel(
        "Searching", "weather", "Searched"
    ),
    "connector_web_search.web_search": _ToolLabel("Searching", "the web", "Searched"),
}


def _project_labeled(detail: GenericEffectDetail, label: _ToolLabel) -> _ProjectedCall:
    semantic = detail.model_copy(update={"display": label.call_display()})

    def project_result(state: CompletedEffectState) -> CompletedEffectState:
        # The Harness stamps the raw tool key into the settled result message;
        # swap it for the human wording, leaving any real result text intact.
        if state.display.message != detail.tool_name:
            return state
        display = state.display.model_copy(
            update={"verb": label.settled_verb, "message": label.noun}
        )
        return state.model_copy(update={"display": display})

    return _ProjectedCall(detail=semantic, project_result=project_result)


_CALL_PROJECTORS: dict[str, Callable[[GenericEffectDetail], _ProjectedCall]] = {
    "read_file": _project_read,
    "file_system.read_file": _project_read,
    "write_file": _project_write,
    "file_system.write_file": _project_write,
    "search_replace": _project_search_replace,
    "file_system.search_replace": _project_search_replace,
    "bash": _project_shell,
    "file_system.bash": _project_shell,
    "skill.read": _project_skill,
}

_TOOL_CATEGORIES: dict[str, UnifiedToolCategory] = {
    "read_file": "file_read",
    "file_system.read_file": "file_read",
    "write_file": "file_write",
    "file_system.write_file": "file_write",
    "search_replace": "file_edit",
    "file_system.search_replace": "file_edit",
    "bash": "shell",
    "file_system.bash": "shell",
    "skill.read": "skill",
}


__all__ = [
    "UnifiedToolCategory",
    "project_unified_history_entry",
    "unified_tool_category",
]
