//! The bottom app `/branch` opens with the command that resumes the forked copy.

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;

#[derive(Default)]
pub struct BranchApp {
    pub open: bool,
    pub session_id: String,
}

pub fn open(app: &mut App, session_id: &str) {
    app.branch_app = BranchApp {
        open: true,
        session_id: session_id.to_owned(),
    };
}

pub fn resume_command(session_id: &str) -> String {
    format!("vibe --resume {session_id}")
}

pub fn handle_key(app: &mut App, key: KeyEvent) {
    if matches!(key.code, KeyCode::Esc | KeyCode::Enter) {
        app.branch_app.open = false;
    }
}
