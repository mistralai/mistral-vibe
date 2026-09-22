//! Workspace-trust gate shown before the session opens (Python `TrustFolderApp`).

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use tokio::sync::mpsc;

use crate::app::{App, Status};
use crate::selection;
use crate::server::WorkspaceTrustDetails;

pub const TRUST_REPO: &str = "trust_repo";
pub const TRUST_CWD: &str = "trust_cwd";
pub const DECLINE: &str = "decline";

#[derive(Default)]
pub struct TrustFolders {
    pub open: bool,
    pub details: Option<WorkspaceTrustDetails>,
    /// `(decision, label)` in display order, like Python's `_build_options`.
    pub options: Vec<(&'static str, String)>,
    pub selected: usize,
    /// First visible row of the detected-files region, like Python's `VerticalScroll`.
    pub scroll: usize,
    /// Largest useful `scroll`, written by the last draw.
    pub scroll_max: usize,
    /// Answers the handshake task waits on before it opens the session.
    pub tx: Option<mpsc::Sender<String>>,
}

impl TrustFolders {
    /// The repo line is hidden when it would only repeat the cwd.
    pub fn repo_root(&self) -> Option<&str> {
        let details = self.details.as_ref()?;
        let repo_root = details.repo_root.as_deref()?;
        (repo_root != details.cwd).then_some(repo_root)
    }

    pub fn offer_repo_trust(&self) -> bool {
        let offered = self
            .details
            .as_ref()
            .is_some_and(|d| d.available_decisions.iter().any(|d| d == TRUST_REPO));
        offered && self.repo_root().is_some()
    }

    pub fn repo_explicitly_untrusted(&self) -> bool {
        let untrusted = self
            .details
            .as_ref()
            .is_some_and(|d| d.repo_explicitly_untrusted);
        untrusted && self.repo_root().is_some()
    }

    pub fn title(&self) -> &'static str {
        match self.offer_repo_trust() {
            true => "Trust folder or repository?",
            false => "Trust this folder?",
        }
    }
}

/// Open the gate on the details the handshake read, selecting "Trust folder".
pub fn open(app: &mut App, details: WorkspaceTrustDetails) {
    app.trust.details = Some(details);
    app.trust.options = build_options(&app.trust);
    app.trust.selected = app
        .trust
        .options
        .iter()
        .position(|(decision, _)| *decision == TRUST_CWD)
        .unwrap_or(0);
    app.trust.scroll = 0;
    app.trust.scroll_max = 0;
    app.trust.open = true;
}

fn build_options(trust: &TrustFolders) -> Vec<(&'static str, String)> {
    let mut options = Vec::new();
    if trust.offer_repo_trust() {
        options.push((TRUST_REPO, "Trust full repo".to_owned()));
    }
    options.push((TRUST_CWD, "Trust folder".to_owned()));
    options.push((DECLINE, "Don't trust".to_owned()));
    options
}

/// Ctrl+C / Ctrl+Q, the only keys the pre-session window answers.
fn is_quit(key: KeyEvent) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL)
        && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('q'))
}

/// Move the detected-files region by `delta` rows, clamped to what it can show.
fn scroll_by(app: &mut App, delta: isize) {
    let scroll = app.trust.scroll as isize + delta;
    app.trust.scroll = scroll.clamp(0, app.trust.scroll_max as isize) as usize;
}

/// Scroll the detected-files region by one wheel notch.
pub(crate) fn wheel(app: &mut App, up: bool) {
    scroll_by(app, if up { -2 } else { 2 });
}

/// Handle one key; `true` quits the client, as Python's Ctrl+C/Ctrl+Q do.
pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    let count = app.trust.options.len();
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        return is_quit(key);
    }
    match key.code {
        KeyCode::Left => app.trust.selected = (app.trust.selected + count - 1) % count,
        KeyCode::Right => app.trust.selected = (app.trust.selected + 1) % count,
        KeyCode::Enter => decide(app, app.trust.selected),
        KeyCode::Char(digit @ '1'..='3') => {
            decide(app, digit as usize - '1' as usize);
        }
        _ => {}
    }
    false
}

/// The gate answers only with keys, so the mouse just drives text selection.
pub fn handle_mouse(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Moved => app.view.mouse_position = Some(at),
        MouseEventKind::Down(MouseButton::Left) => selection::press_including_padding(app, at),
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            selection::release(app);
        }
        MouseEventKind::ScrollUp => scroll_by(app, -2),
        MouseEventKind::ScrollDown => scroll_by(app, 2),
        _ => {}
    }
}

/// Answer the handshake and close the gate, so the session can open.
fn decide(app: &mut App, option: usize) {
    let Some((decision, _)) = app.trust.options.get(option) else {
        return;
    };
    if let Some(tx) = app.trust.tx.take() {
        // Nothing is left to open the session, so fail loudly rather than hang.
        if tx.try_send((*decision).to_owned()).is_err() {
            app.session.startup_error = Some("trust gate lost the handshake".to_owned());
            app.set_status(Status::Failed);
        }
    }
    app.trust.open = false;
    app.trust.details = None;
}
