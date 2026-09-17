//! Bottom-bar selection: a single-row column range copied on release.

use std::time::Instant;

use crate::app::{App, Surface};
use crate::selection::{Granularity, Release};

/// A single-row selection in the bottom bar (anchor/head are screen columns).
#[derive(Clone)]
pub struct BottomBarSelection {
    pub anchor: u16,
    pub head: u16,
    /// Set on release: the next render extracts the selected text and copies it.
    pub pending_copy: bool,
    /// Selected text cached at paint time so a copy key can read it without the
    /// frame buffer.
    pub text: String,
}

/// Start a bottom-bar selection at screen cell `at`, feeding the shared
/// click-chain so a double click snaps to a word and a triple click to the row.
pub fn press(app: &mut App, at: (u16, u16)) {
    app.selection.region = None;
    app.chat_input.anchor = None;
    app.selection.press = Some(at);
    app.selection.dragged = false;
    app.selection.granularity = app.selection.chain.press(at, Instant::now());
    app.selection.drag = Some(Surface::BottomBar);
    app.selection.bottom_bar = Some(BottomBarSelection {
        anchor: at.0,
        head: at.0,
        pending_copy: false,
        text: String::new(),
    });
}

/// Extend the selection to screen cell `at`.
pub fn drag(app: &mut App, at: (u16, u16)) {
    if app.selection.press.is_some_and(|press| press != at) {
        app.selection.dragged = true;
    }
    if let Some(sel) = app.selection.bottom_bar.as_mut() {
        sel.head = at.0;
    }
}

/// End the gesture; a moved pointer or a word/row click arms the deferred copy,
/// a plain single click clears it. The shared `release` drives the click chain.
pub(super) fn release(app: &mut App) -> Release {
    match app.selection.bottom_bar.as_mut() {
        Some(sel) if sel.anchor != sel.head || app.selection.granularity != Granularity::Char => {
            sel.pending_copy = true;
            Release::Selected
        }
        _ => {
            app.selection.bottom_bar = None;
            Release::Nothing
        }
    }
}
