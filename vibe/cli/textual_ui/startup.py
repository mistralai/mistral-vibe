from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import CenterMiddle

from vibe.app_server.host import AppServerHost
from vibe.app_server.models import SavedSessionSummary
from vibe.app_server.session import AppServerSession
from vibe.cli.textual_ui.widgets.session_picker import SessionPickerApp
from vibe.setup.trusted_folders.trust_folder_dialog import (
    TrustDialogQuitException,
    TrustFolderApp,
)


@dataclass(frozen=True, slots=True)
class OpenedTextualSession:
    session: AppServerSession
    resumed: bool


@dataclass(frozen=True, slots=True)
class _PickerResult:
    session_id: str | None = None
    aborted: bool = False


class _StartupSessionPicker(App[_PickerResult]):
    CSS_PATH = "app.tcss"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "abort", show=False, priority=True),
        Binding("ctrl+q", "abort", show=False, priority=True),
    ]

    def __init__(
        self, host: AppServerHost, sessions: list[SavedSessionSummary], cwd: str | None
    ) -> None:
        super().__init__()
        self._host = host
        self._sessions = sessions
        self._cwd = cwd

    def compose(self) -> ComposeResult:
        with CenterMiddle():
            yield SessionPickerApp(
                sessions=self._sessions,
                latest_messages={
                    session.session_id: session.preview for session in self._sessions
                },
                cwd=self._cwd,
            )

    def action_abort(self) -> None:
        self.exit(result=_PickerResult(aborted=True))

    def on_session_picker_app_session_selected(
        self, event: SessionPickerApp.SessionSelected
    ) -> None:
        self.exit(result=_PickerResult(session_id=event.session_id))

    def on_session_picker_app_cancelled(
        self, event: SessionPickerApp.Cancelled
    ) -> None:
        del event
        self.exit(result=_PickerResult())

    async def on_session_picker_app_session_delete_requested(
        self, event: SessionPickerApp.SessionDeleteRequested
    ) -> None:
        picker = self.query_one(SessionPickerApp)
        try:
            await self._host.delete_session(event.session_id)
        except Exception as exc:
            picker.clear_pending_delete(event.option_id)
            self.notify(str(exc), severity="error", markup=False)
            return
        picker.remove_session(event.option_id)
        if not picker.has_sessions:
            self.exit(result=_PickerResult())


async def open_textual_session(
    host: AppServerHost,
    *,
    prompt_for_workspace_trust: bool,
    show_resume_picker: bool,
    initially_resuming: bool,
) -> OpenedTextualSession | None:
    try:
        if prompt_for_workspace_trust and not await _resolve_workspace_trust(host):
            await host.close()
            return None

        if not show_resume_picker:
            return OpenedTextualSession(
                session=await host.open_session(), resumed=initially_resuming
            )

        sessions = await host.list_sessions(host.cwd)
        if not sessions:
            return OpenedTextualSession(
                session=await host.start_session(), resumed=False
            )
        result = await _StartupSessionPicker(host, sessions, host.cwd).run_async(
            inline=True
        )
        if result is None or result.aborted:
            await host.close()
            return None
        if result.session_id is None:
            return OpenedTextualSession(
                session=await host.start_session(), resumed=False
            )
        return OpenedTextualSession(
            session=await host.resume_session(result.session_id), resumed=True
        )
    except BaseException:
        await host.close()
        raise


async def _resolve_workspace_trust(host: AppServerHost) -> bool:
    status = await host.trust_status(host.cwd)
    details = status.details
    if details is None:
        return True
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
        return False
    if decision is None:
        return False
    await host.decide_trust(decision, cwd=details.cwd)
    return True
