"""Project the local runtime's history entries into Vibe's public entries.

Both the adapter and the Session API history projection build public history
from the runtime's raw entries, so the projection lives here, outside both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from vibe.app_server._approval_permissions import available_path_scopes
from vibe.app_server._effect_output_cap import capped_effect_entry
from vibe.app_server._loop_prompt import project_loop_message
from vibe.app_server._skill_invocation import (
    invoked_skill_payloads,
    skill_invocation_display_names,
    without_skill_invocation_display,
)
from vibe.app_server._tool_projection import project_effect_output_value
from vibe.app_server._unified_tool_observability import add_unified_tool_projection
from vibe.app_server._unified_tool_projection import (
    is_large_output_receipt,
    project_unified_history_entry,
    unified_tool_category,
)
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    CompletedEffectState,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    SkillEffectDetail,
    SkillEffectInput,
    TextContentBlock,
    validate_history_entry,
)
from vibe.utils.tool_presentation import EffectCallDisplay, EffectResultDisplay


def project_raw_history_entry(
    raw_entry: Mapping[str, Any], display_roots: Sequence[Path]
) -> list[PublicHistoryEntry]:
    normalized = dict(raw_entry)
    details = normalized.get("details")
    if (
        normalized.get("type") == "notice"
        and "detail" not in normalized
        and isinstance(details, dict)
        and details.get("kind") == "scheduled_loop_fired"
    ):
        normalized["detail"] = normalized.pop("details")
    return project_history_entries(normalized, display_roots)


def project_history_entry(
    value: object, display_roots: Sequence[Path]
) -> PublicHistoryEntry:
    source = validate_history_entry(value)
    category = unified_tool_category(source)
    projected = project_unified_history_entry(source, display_roots)
    if category is not None:
        add_unified_tool_projection(
            category=category,
            # A large-output receipt keeps its header but loses its result.
            outcome=(
                "degraded"
                if projected == source or is_large_output_receipt(source)
                else "projected"
            ),
        )
    return capped_effect_entry(
        with_available_path_scopes(
            project_loop_message(normalize_effect_output(projected))
        )
    )


def with_available_path_scopes[HistoryEntryT: PublicHistoryEntry](
    entry: HistoryEntryT,
) -> HistoryEntryT:
    if not isinstance(entry, PublicCallbackEntry) or not isinstance(
        entry.detail, ApprovalCallbackDetail
    ):
        return entry
    choices = available_path_scopes(entry.detail.required_permissions)
    if entry.detail.path_scope_choices == choices:
        return entry
    return entry.model_copy(
        update={
            "detail": entry.detail.model_copy(update={"path_scope_choices": choices})
        }
    )


def project_history_entries(
    value: object, display_roots: Sequence[Path]
) -> list[PublicHistoryEntry]:
    """Project one Harness entry and expand user-invoked skills for clients.

    Unified Core must receive each skill body as user content because it has no
    API for inserting a synthetic tool exchange. The public transcript need not
    expose that transport detail: present the literal prompt followed by the
    same settled skill effect that a model-initiated load produces, one per
    invoked skill.
    """
    entry = project_history_entry(value, display_roots)
    if not isinstance(entry, PublicMessageEntry):
        return [entry]
    public_display = without_skill_invocation_display(entry.user_display_content)
    payloads = _invoked_skill_message_payloads(entry)
    indices = {index for index, _name in payloads}
    message = entry.model_copy(
        update={
            "content": [
                block
                for index, block in enumerate(entry.content)
                if index not in indices
            ],
            "user_display_content": public_display,
        }
    )
    return [
        message,
        *(
            capped_effect_entry(
                _invoked_skill_effect(
                    message, name, cast(TextContentBlock, entry.content[index]).text
                )
            )
            for index, name in payloads
        ),
    ]


def _invoked_skill_message_payloads(entry: PublicMessageEntry) -> list[tuple[int, str]]:
    if entry.role != "user" or not entry.content:
        return []
    return invoked_skill_payloads(
        [
            block.text if isinstance(block, TextContentBlock) else None
            for block in entry.content
        ],
        skill_invocation_display_names(entry.user_display_content),
    )


def _invoked_skill_effect(
    message: PublicMessageEntry, name: str, output_text: str
) -> PublicEffectEntry:
    return PublicEffectEntry(
        id=f"{message.id}:invoked-skill:{name}",
        session_id=message.session_id,
        turn_id=message.turn_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        related_entry_id=message.id,
        title="skill",
        detail=SkillEffectDetail(
            tool_name="skill",
            input=SkillEffectInput(name=name),
            display=EffectCallDisplay(
                summary=f"Loading skill: {name}",
                verb="Loading",
                message=f"skill: {name}",
                settled_verb="Loaded",
                settled_message=f"skill: {name}",
                status_text="Loading skill",
            ),
        ),
        state=CompletedEffectState(
            output_text=output_text,
            display=EffectResultDisplay(
                success=True, verb="Loaded", message=f"skill: {name}"
            ),
        ),
    )


def normalize_effect_output(entry: PublicHistoryEntry) -> PublicHistoryEntry:
    """Re-project a completed effect's output through the shared effect projection.

    A post_tool hook can leave a tool effect's ``output`` as the raw RustToolResult wire
    shape, which fits no client's output model. Routing it through
    ``project_effect_output_value`` degrades such a result to None; valid native outputs
    are unchanged (the projection is idempotent for them).
    """
    if not isinstance(entry, PublicEffectEntry):
        return entry
    state = entry.state
    if not isinstance(state, CompletedEffectState) or state.output is None:
        return entry
    reprojected = project_effect_output_value(entry.detail.kind, state.output)
    if reprojected == state.output:
        return entry
    return entry.model_copy(
        update={"state": state.model_copy(update={"output": reprojected})}
    )


__all__ = [
    "normalize_effect_output",
    "project_history_entries",
    "project_history_entry",
    "project_raw_history_entry",
    "with_available_path_scopes",
]
