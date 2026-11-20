//! The `/skills` footer: moves, Enter, the shown skill's keys, the screen's own keys, then Esc.

use super::{files, list, page, picker, Screen, State};
use crate::hints::{self, action, key, Hint};

/// The deletion confirmation's footer.
const CONFIRM: &[Hint] = &[
    (key::LEFT_RIGHT, action::SELECT),
    (key::ENTER, action::CONFIRM),
    hints::CANCEL,
];

/// The footer of whichever screen is up, with how to scroll the body before its Esc while the body overflows its pane; a pending deletion has its own.
pub fn help(state: &State) -> Vec<Hint> {
    if state.confirm.is_some() {
        return CONFIRM.to_vec();
    }
    let mut hints = match state.screen() {
        Screen::List => return list::help(state),
        Screen::Page(page) => {
            let back = [hints::BACK].to_vec();
            [page::help(state, page), files::pin_hints(state), back].concat()
        }
        Screen::Picker(_, picker) => picker::help(picker),
    };
    if state.pane.overflows() {
        let esc = hints.len().saturating_sub(1);
        hints.insert(esc, scroll_hint(state));
    }
    hints
}

/// The arrows on a page with at most one row, else PgUp/PgDn.
fn scroll_hint(state: &State) -> Hint {
    match state.screen() {
        Screen::Page(page) if !page::movable(state, page) => (key::NAV, action::SCROLL),
        _ => (key::PAGE_UP_DOWN, action::SCROLL),
    }
}
