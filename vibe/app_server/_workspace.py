from __future__ import annotations

from pathlib import Path
import re
from typing import cast
from urllib.parse import urlsplit

from vibe.app_server.models import (
    ContentBlock,
    ImageAttachment,
    MentionStats,
    PreparedPrompt,
    ResourceContentBlock,
    TextContentBlock,
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
    build_path_prompt_payload,
    build_title_segments,
)
from vibe.core.autocompletion.path_prompt_adapter import extract_image_resources
from vibe.core.paths import TRUSTED_FOLDERS_FILE
from vibe.core.session.image_snapshot import ImageSnapshotError, snapshot_image
from vibe.core.session.title_format import (
    MentionSegment,
    TitleSegment,
    format_session_title,
)
from vibe.core.trusted_folders import (
    TrustedFoldersManager,
    WorkspaceTrustDecision as CoreWorkspaceTrustDecision,
    WorkspaceTrustPrompt,
    apply_workspace_trust_decision,
    available_workspace_trust_decisions,
    find_untrusted_config_dirs,
    maybe_build_workspace_trust_prompt,
)
from vibe.user_content import UserResourceLink
from vibe.utils.images import MAX_IMAGES_PER_MESSAGE

_LINE_FRAGMENT_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$")


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
    payload = build_path_prompt_payload(message, base_dir=agent_loop.cwd)
    images = _snapshot_images(agent_loop, payload)
    model = agent_loop.config.get_active_model()
    if images and not model.supports_images:
        raise PromptPreparationError(
            f"Model `{model.alias}` does not support images. "
            "Switch with /model or remove the attachment."
        )
    title = None
    if agent_loop.session_logger.needs_initial_auto_title():
        segments = (
            _structured_title_segments(title_content, base_dir=agent_loop.cwd)
            if title_content is not None
            else build_title_segments(message, base_dir=agent_loop.cwd)
        )
        title = format_session_title(segments) or None
    return PreparedPrompt(
        display_text=message,
        prompt_text=message,
        images=images,
        auto_title=title,
        mentions=_mention_stats(payload),
    )


def _structured_title_segments(
    content: list[ContentBlock], *, base_dir: Path
) -> list[TitleSegment]:
    segments: list[TitleSegment] = []
    for block in content:
        match block:
            case TextContentBlock(text=text):
                segments.extend(build_title_segments(text, base_dir=base_dir))
            case ResourceContentBlock(resource=resource):
                uri, start_line, end_line = _resource_location(resource.uri)
                name = (
                    resource.name
                    if isinstance(resource, UserResourceLink) and resource.name
                    else Path(urlsplit(uri).path).name
                )
                if name:
                    segments.append(MentionSegment(name, start_line, end_line))
    return segments


def _resource_location(uri: str) -> tuple[str, int | None, int | None]:
    parts = urlsplit(uri)
    match = _LINE_FRAGMENT_RE.fullmatch(parts.fragment)
    if match is None:
        return uri, None, None
    return (
        parts._replace(fragment="").geturl(),
        int(match.group(1)),
        int(match.group(2)) if match.group(2) is not None else None,
    )


def _snapshot_images(
    agent_loop: AgentLoop, payload: PathPromptPayload
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
                resource.path,
                alias=resource.alias,
                session_dir=agent_loop.session_logger.session_dir,
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
