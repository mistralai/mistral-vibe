//! The `/plugins` search field, following the shared list search model.

use crossterm::event::KeyEvent;

use crate::app::App;
use crate::search_field::{self, Outcome};

/// Route a key in the list view; false when the list handles it.
pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if app.plugins.viewing.is_some() {
        return false;
    }
    match search_field::handle_key(&mut app.plugins.filter, &key) {
        Outcome::Pass => false,
        Outcome::Consumed => true,
        Outcome::Filtered => {
            super::reset_highlight(app);
            true
        }
    }
}

pub fn paste(app: &mut App, text: &str) {
    if app.plugins.viewing.is_some() || !search_field::paste(&mut app.plugins.filter, text) {
        return;
    }
    super::reset_highlight(app);
}
