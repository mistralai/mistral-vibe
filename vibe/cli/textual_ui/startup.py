from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibe.app_server.host import AppServerHost
from vibe.app_server.session import AppServerSession
from vibe.setup.trusted_folders.trust_folder_dialog import (
    TrustDialogQuitException,
    TrustFolderApp,
)


@dataclass(frozen=True, slots=True)
class OpenedTextualSession:
    session: AppServerSession
    resumed: bool
    showed_trust_prompt: bool = False
    showed_resume_picker: bool = False
    resume_session_id: str | None = None
    continue_latest: bool = False


@dataclass(frozen=True, slots=True)
class SessionOpenPlan:
    method: str  # "open" | "start" | "resume"
    session_id: str | None = None
    resumed: bool = False
    showed_trust_prompt: bool = False
    showed_resume_picker: bool = False
    resume_session_id: str | None = None
    continue_latest: bool = False


async def resolve_session_open_plan(
    host: AppServerHost,
    *,
    prompt_for_workspace_trust: bool,
    show_resume_picker: bool,
    initially_resuming: bool,
    resume_session_id: str | None = None,
) -> SessionOpenPlan | None:
    showed_trust_prompt = False
    try:
        if prompt_for_workspace_trust:
            trust_granted, showed_trust_prompt = await _resolve_workspace_trust(host)
            if not trust_granted:
                await host.close()
                return None

        if not show_resume_picker and not initially_resuming:
            return SessionOpenPlan(
                method="open",
                resumed=initially_resuming,
                showed_trust_prompt=showed_trust_prompt,
            )

        # When --resume (picker), --resume <id>, or --continue is used, start a
        # fresh session in the background. Empty sessions are never persisted to
        # disk, so no orphan files are created if the user picks an existing
        # session or cancels. For --resume <id> and --continue, the fresh
        # session renders instantly and the actual resume happens in-app via the
        # auto-resume descriptor, reusing the fast in-place rebind path.
        return SessionOpenPlan(
            method="start",
            showed_trust_prompt=showed_trust_prompt,
            showed_resume_picker=show_resume_picker,
            resume_session_id=resume_session_id if initially_resuming else None,
            continue_latest=(initially_resuming and resume_session_id is None),
        )
    except BaseException:
        await host.close()
        raise


async def _execute_session_open_plan(
    host: AppServerHost, plan: SessionOpenPlan
) -> AppServerSession:
    if plan.method == "open":
        return await host.open_session()
    if plan.method == "start":
        return await host.start_session()
    # method == "resume"
    assert plan.session_id is not None
    return await host.resume_session(plan.session_id)


async def open_textual_session(
    host: AppServerHost,
    *,
    prompt_for_workspace_trust: bool,
    show_resume_picker: bool,
    initially_resuming: bool,
    resume_session_id: str | None = None,
) -> OpenedTextualSession | None:
    plan = await resolve_session_open_plan(
        host,
        prompt_for_workspace_trust=prompt_for_workspace_trust,
        show_resume_picker=show_resume_picker,
        initially_resuming=initially_resuming,
        resume_session_id=resume_session_id,
    )
    if plan is None:
        return None
    session = await _execute_session_open_plan(host, plan)
    return OpenedTextualSession(
        session=session,
        resumed=plan.resumed,
        showed_trust_prompt=plan.showed_trust_prompt,
        showed_resume_picker=plan.showed_resume_picker,
        resume_session_id=plan.resume_session_id,
        continue_latest=plan.continue_latest,
    )


async def _resolve_workspace_trust(host: AppServerHost) -> tuple[bool, bool]:
    """Returns (trust_granted, prompt_shown)."""
    status = await host.trust_status(host.cwd)
    details = status.details
    if details is None:
        return True, False
    dialog = TrustFolderApp(
        cwd=Path(details.cwd),
        repo_root=Path(details.repo_root) if details.repo_root is not None else None,
        detected_files=details.detected_files,
        repo_detected_files=details.repo_detected_files,
        offer_repo_trust="trust_repo" in details.available_decisions,
        repo_explicitly_untrusted=details.repo_explicitly_untrusted,
        settings_path=details.settings_path,
    )
    try:
        decision = await dialog.run_trust_dialog_async()
    except TrustDialogQuitException:
        return False, True
    if decision is None:
        return False, True
    await host.decide_trust(decision, cwd=details.cwd)
    return True, True
