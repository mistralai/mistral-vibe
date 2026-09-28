//! The retried turn's continuation: merging its hidden output into the
//! interrupted row, and reconciling that merge across session snapshots.

use crate::app::App;

/// Recompute the interrupted row from the hidden continuation entry; called
/// after each add/update touching the retried turn's output.
pub fn merge_continuation(app: &mut App) {
    if let Some(continuation) = app.session.retry_continuation.as_ref() {
        app.view.transcript.merge_continuation(
            &continuation.dst_id,
            &continuation.src_id,
            &continuation.base_content,
        );
    }
}

/// ADR 0009: a live same-session snapshot retains the already-loaded state, so
/// the retry presentation and continuation survive it; only a session handoff
/// or explicit resync replaces them. The live merge keeps both of the
/// continuation's rows across a bounded snapshot page — the reload dropped the
/// hidden flag, so it is re-applied — and drops the continuation only when the
/// snapshot genuinely no longer contains one of its rows.
pub fn reconcile_snapshot(app: &mut App, same_session: bool) {
    if !same_session {
        app.session.retry_presentation = None;
        app.session.retry_continuation = None;
        return;
    }
    let Some(continuation) = app.session.retry_continuation.clone() else {
        return;
    };
    if app.view.transcript.contains(&continuation.dst_id)
        && app.view.transcript.contains(&continuation.src_id)
    {
        app.view.transcript.hide(&continuation.src_id);
        merge_continuation(app);
    } else {
        app.session.retry_continuation = None;
    }
}
