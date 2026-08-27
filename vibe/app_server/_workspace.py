from __future__ import annotations

from pathlib import Path
from typing import cast

from vibe.app_server.models import (
    ContentBlock,
    ImageAttachment,
    MentionStats,
    PreparedPrompt,
    ResourceContentBlock,
    WorkspaceTrustDecision,
    WorkspaceTrustDetails,
)
from vibe.app_server.protocol import (
    WorkspaceTrustStatusResponse,
    WorkspaceUntrustedConfigResponse,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.autocompletion.path_prompt import (
    PathPromptPayload,
    PathResource,
    build_path_prompt_payload,
)
from vibe.core.autocompletion.path_prompt_adapter import extract_image_resources
from vibe.core.paths import TRUSTED_FOLDERS_FILE
from vibe.core.session.image_snapshot import ImageSnapshotError, snapshot_image
from vibe.core.trusted_folders import (
    TrustedFoldersManager,
    WorkspaceTrustDecision as CoreWorkspaceTrustDecision,
    WorkspaceTrustPrompt,
    apply_workspace_trust_decision,
    available_workspace_trust_decisions,
    find_untrusted_config_dirs,
    maybe_build_workspace_trust_prompt,
)
from vibe.user_content import UserTextResource
from vibe.utils.images import MAX_IMAGES_PER_MESSAGE
from vibe.utils.io import BoundedReadResult, read_lines_safe, read_lines_safe_async

_MENTIONED_FILE_MAX_BYTES = 50 * 1024
_MENTIONED_FILE_LINE_LIMIT = 2000
_MENTIONED_FILE_MAX_FILES = 8
_TRUNCATED_FILE_NOTE = "\n\n[File mention truncated to fit context limits.]"


class PromptPreparationError(ValueError):
    pass


class WorkspaceTrustError(ValueError):
    pass


def read_workspace_trust(
    cwd: Path, trust_store: TrustedFoldersManager
) -> WorkspaceTrustStatusResponse:
    resolved = cwd.expanduser().resolve()
    status = trust_store.trust_status(resolved)
    if status != "untrusted":
        return WorkspaceTrustStatusResponse(status=status.value)

    prompt = maybe_build_workspace_trust_prompt(
        resolved, include_explicitly_untrusted=True, manager=trust_store
    )
    return WorkspaceTrustStatusResponse(
        status=status.value,
        details=_workspace_trust_details(prompt) if prompt is not None else None,
    )


def decide_workspace_trust(
    cwd: Path, decision: WorkspaceTrustDecision, trust_store: TrustedFoldersManager
) -> WorkspaceTrustStatusResponse:
    resolved = cwd.expanduser().resolve()
    prompt = maybe_build_workspace_trust_prompt(
        resolved, include_explicitly_untrusted=True, manager=trust_store
    )
    if prompt is None:
        raise WorkspaceTrustError("No workspace trust decision is available")

    core_decision = CoreWorkspaceTrustDecision(decision)
    available = available_workspace_trust_decisions(prompt, include_session=False)
    if core_decision not in available:
        raise WorkspaceTrustError(f"Unsupported trust decision: {decision}")

    apply_workspace_trust_decision(prompt, core_decision, manager=trust_store)
    return read_workspace_trust(resolved, trust_store)


def read_untrusted_config_dirs(
    cwd: Path, trust_store: TrustedFoldersManager
) -> WorkspaceUntrustedConfigResponse:
    resolved = cwd.expanduser().resolve()
    dirs = find_untrusted_config_dirs(resolved, manager=trust_store)
    return WorkspaceUntrustedConfigResponse(
        dirs=[str(d) for d in dirs], settings_path=str(TRUSTED_FOLDERS_FILE.path)
    )


def _workspace_trust_details(prompt: WorkspaceTrustPrompt) -> WorkspaceTrustDetails:
    return WorkspaceTrustDetails(
        cwd=str(prompt.cwd.resolve()),
        repo_root=str(prompt.repo_root.resolve()) if prompt.repo_root else None,
        detected_files=prompt.detected_files,
        repo_detected_files=prompt.repo_detected_files,
        repo_explicitly_untrusted=prompt.repo_explicitly_untrusted,
        settings_path=str(TRUSTED_FOLDERS_FILE.path),
        available_decisions=[
            cast(WorkspaceTrustDecision, decision.value)
            for decision in available_workspace_trust_decisions(
                prompt, include_session=False
            )
        ],
    )


def prepare_prompt(
    agent_loop: AgentLoop, message: str, title_content: list[ContentBlock] | None = None
) -> PreparedPrompt:
    model = agent_loop.config.get_active_model()
    return prepare_prompt_from_context(
        message,
        cwd=agent_loop.cwd,
        session_dir=agent_loop.session_logger.session_dir,
        model_alias=model.alias,
        model_display_name=model.display_name,
        model_supports_images=model.supports_images,
        needs_initial_auto_title=agent_loop.session_logger.needs_initial_auto_title(),
        title_content=title_content,
    )


def prepare_prompt_from_context(
    message: str,
    *,
    cwd: Path,
    session_dir: Path | None,
    model_alias: str,
    model_supports_images: bool,
    needs_initial_auto_title: bool,
    model_display_name: str | None = None,
    title_content: list[ContentBlock] | None = None,
) -> PreparedPrompt:
    payload = build_path_prompt_payload(message, base_dir=cwd)
    images = _snapshot_images(session_dir, payload)
    if images and not model_supports_images:
        raise PromptPreparationError(
            f"Model `{model_display_name or model_alias}` does not support images. "
            "Switch with /model or remove the attachment."
        )
    # The title is left unset here; it is generated in the background by the
    # agent loop once there is a transcript to summarize.
    return PreparedPrompt(
        display_text=message,
        prompt_text=message,
        images=images,
        auto_title=None,
        mentions=_mention_stats(payload),
    )


def mentioned_file_content_blocks(
    message: str, *, base_dir: Path
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for resource in _mentioned_file_resources(message, base_dir=base_dir):
        try:
            result = read_lines_safe(
                resource.path,
                limit=_MENTIONED_FILE_LINE_LIMIT,
                max_bytes=_MENTIONED_FILE_MAX_BYTES,
            )
        except OSError as exc:
            raise PromptPreparationError(
                f"Failed to attach file {resource.alias}: {exc}"
            ) from exc
        blocks.append(_mentioned_file_content_block(resource.path, result))
    return blocks


async def mentioned_file_content_blocks_async(
    message: str, *, base_dir: Path
) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for resource in _mentioned_file_resources(message, base_dir=base_dir):
        try:
            result = await read_lines_safe_async(
                resource.path,
                limit=_MENTIONED_FILE_LINE_LIMIT,
                max_bytes=_MENTIONED_FILE_MAX_BYTES,
            )
        except OSError as exc:
            raise PromptPreparationError(
                f"Failed to attach file {resource.alias}: {exc}"
            ) from exc
        blocks.append(_mentioned_file_content_block(resource.path, result))
    return blocks


def _mentioned_file_resources(message: str, *, base_dir: Path) -> list[PathResource]:
    root = base_dir.expanduser().resolve()
    payload = build_path_prompt_payload(message, base_dir=root)
    resources = [resource for resource in payload.resources if resource.kind == "file"]
    if len(resources) > _MENTIONED_FILE_MAX_FILES:
        raise PromptPreparationError(
            f"Too many file mentions: {_MENTIONED_FILE_MAX_FILES} maximum"
        )
    for resource in resources:
        if not resource.path.resolve().is_relative_to(root):
            raise PromptPreparationError(
                f"Cannot attach file outside the workspace: {resource.alias}"
            )
    return resources


def _mentioned_file_content_block(
    path: Path, result: BoundedReadResult
) -> ResourceContentBlock:
    text = "\n".join(result.lines)
    if result.was_truncated:
        text += _TRUNCATED_FILE_NOTE
    return ResourceContentBlock(resource=UserTextResource(uri=path.as_uri(), text=text))


def _snapshot_images(
    session_dir: Path | None, payload: PathPromptPayload
) -> list[ImageAttachment]:
    resources = extract_image_resources(payload)
    if len(resources) > MAX_IMAGES_PER_MESSAGE:
        raise PromptPreparationError(
            f"Too many image attachments (got {len(resources)}, "
            f"max {MAX_IMAGES_PER_MESSAGE})."
        )
    attachments: list[ImageAttachment] = []
    for resource in resources:
        try:
            attachment = snapshot_image(
                resource.path, alias=resource.alias, session_dir=session_dir
            )
        except ImageSnapshotError as exc:
            raise PromptPreparationError(
                f"Failed to attach image {resource.alias}: {exc}"
            ) from exc
        attachments.append(
            ImageAttachment.model_validate(attachment.model_dump(mode="json"))
        )
    return attachments


def _mention_stats(payload: PathPromptPayload) -> MentionStats:
    context_types: dict[str, int] = {}
    file_extensions: dict[str, int] = {}
    for resource in payload.all_resources:
        context_types[resource.kind] = context_types.get(resource.kind, 0) + 1
        if resource.kind != "file":
            continue
        extension = resource.path.suffix
        file_extensions[extension] = file_extensions.get(extension, 0) + 1
    return MentionStats(
        count=len(payload.all_resources),
        context_types=context_types,
        file_extensions=file_extensions,
    )
