//! The `/plugins` search field, following the shared list search model.

use crossterm::event::KeyEvent;

use crate::app::App;
use crate::search_field;

/// Route a key in the list view; false when the list handles it.
pub fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if app.plugins.viewing.is_some() {
        return false;
    }
    search_field::handle_key(&mut app.plugins.filter, &key).handled(|| super::reset_highlight(app))
}

pub fn paste(app: &mut App, text: &str) {
    if app.plugins.viewing.is_some() || !search_field::paste(&mut app.plugins.filter, text) {
        return;
    }
    super::reset_highlight(app);
}
