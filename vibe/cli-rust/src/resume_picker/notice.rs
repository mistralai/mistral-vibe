//! Swapping the picker's preview against the transcript Esc restores.

use crate::app::App;
use crate::commands::submission;
use crate::server::PublicSessionState;
use crate::transcript::local;

/// Re-anchor an open picker on the session the handshake finally started: it
/// becomes what Esc restores, while the preview stays on screen. Loading it
/// without putting the preview back would blank the transcript for a frame.
pub fn rebase(app: &mut App, state: &PublicSessionState) {
    let preview = app.view.transcript.snapshot();
    app.view.transcript.load_snapshot(state);
    app.resume_picker.transcript = Some(app.view.transcript.snapshot());
    app.view.transcript.restore(preview);
}

/// Settle an in-flight resume exactly once; false means it was cancelled and its
/// answer must be dropped.
pub(super) fn finish_resume(app: &mut App) -> bool {
    if !std::mem::take(&mut app.resume_picker.resuming) {
        return false;
    }
    app.commit_finished();
    true
}

pub(super) fn close(app: &mut App) {
    finish_resume(app);
    app.resume_picker.open = false;
    let previewing = app.resume_picker.previewing;
    if let Some(transcript) = app.resume_picker.transcript.take() {
        if previewing {
            // Python rebuilds the transcript from the current session on cancel,
            // dropping notices (e.g. a delete confirmation) mounted over the preview.
            app.view.transcript.restore(transcript);
        } else {
            // No preview replaced the log, so keep notices mounted while open.
            let notices = local::preserve(&app.view.transcript);
            app.view.transcript.restore(transcript);
            local::restore(&mut app.view.transcript, notices);
        }
        // Exiting the preview is Python's transcript rebuild: re-decide the
        // custom-tools deprecation on the session restored underneath.
        crate::startup::banners::rebuild_custom_tools_deprecation(app);
    }
}

/// Mount a notice on the transcript Esc restores rather than on the preview
/// covering it: Python mounts into the live log, which the preview hides.
pub(super) fn behind(app: &mut App, mount: impl FnOnce(&mut App)) {
    let Some(saved) = app.resume_picker.transcript.take() else {
        mount(app);
        return;
    };
    let preview = app.view.transcript.snapshot();
    app.view.transcript.restore(saved);
    mount(app);
    app.resume_picker.transcript = Some(app.view.transcript.snapshot());
    app.view.transcript.restore(preview);
}

pub(super) fn result(app: &mut App, text: &str) {
    local::add_command_result(
        &mut app.view.transcript,
        &submission::new_message_id(),
        text,
    );
}

pub(super) fn error(app: &mut App, text: &str) {
    local::add_command_error(
        &mut app.view.transcript,
        &submission::new_message_id(),
        text,
    );
}
