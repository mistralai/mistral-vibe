//! `r` on the Sessions page: a modal that edits the highlighted session's title, wrapped so all of it shows.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use crate::app::App;
use crate::chat_input::{self, Action};
use crate::server::Client;
use crate::ui::composer_layout::ComposerLayout;

/// The title being edited, with its caret and where it was painted last frame.
pub struct Rename {
    /// The session it renames.
    pub id: String,
    pub text: String,
    pub cursor: usize,
    pub anchor: Option<usize>,
    /// First shown row, and the text's area last frame, for the wheel and clicks.
    pub scroll: usize,
    pub area: Rect,
}

impl Rename {
    pub fn layout(&self) -> ComposerLayout<'_> {
        // Wrapped at words like the composer, one column kept for a trailing caret.
        ComposerLayout::new(&self.text, "", self.cursor, self.area.width.max(1) + 1)
    }
}

/// Open the modal on the highlighted session, its caret at the end of the title.
pub fn start(app: &mut App) {
    let Some(session) = super::selected(app) else {
        return;
    };
    let text = session.title.clone();
    app.sessions_page.rename = Some(Rename {
        id: session.id.clone(),
        cursor: text.len(),
        text,
        anchor: None,
        scroll: 0,
        area: Rect::default(),
    });
}

pub fn key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let Some(rename) = app.sessions_page.rename.as_mut() else {
        return;
    };
    match key.code {
        KeyCode::Esc => app.sessions_page.rename = None,
        KeyCode::Enter if key.modifiers.is_empty() => super::actions::rename(app, client),
        KeyCode::Up | KeyCode::Down => {
            (rename.cursor, _) = rename.layout().vertical_offset(key.code == KeyCode::Down);
            rename.anchor = None;
        }
        _ => {
            // A title is one line: newline keys insert nothing.
            if let Some(action) =
                crate::keymap::action_for(&key).filter(|action| *action != Action::Insert('\n'))
            {
                apply(rename, &action);
            }
        }
    }
}

/// A paste goes in as one line, as a title is.
pub fn paste(rename: &mut Rename, text: &str) {
    for ch in crate::utils::text::single_line(text).chars() {
        apply(rename, &Action::Insert(ch));
    }
}

fn apply(rename: &mut Rename, action: &Action) {
    chat_input::apply(
        action,
        &mut rename.text,
        &mut rename.cursor,
        &mut rename.anchor,
    );
}

/// A click in the title puts the caret under the pointer; a click outside the modal leaves it open.
pub fn mouse(app: &mut App, event: MouseEvent) {
    let Some(rename) = app.sessions_page.rename.as_mut() else {
        return;
    };
    let area = rename.area;
    if event.kind != MouseEventKind::Down(MouseButton::Left)
        || !area.contains((event.column, event.row).into())
    {
        return;
    }
    let row = rename.scroll + usize::from(event.row - area.y);
    let column = usize::from(event.column - area.x);
    rename.cursor = rename.layout().offset_at(row, column);
    rename.anchor = None;
}
