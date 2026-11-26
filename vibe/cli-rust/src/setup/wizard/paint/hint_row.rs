//! The onboarding panel's hint row: the screen's keys, wrapped to the panel instead of cut.

use ratatui::layout::Rect;
use ratatui::Frame;

use super::browser_sign_in;
use crate::setup::wizard::SignInVariant;
use crate::ui::hint_line;

/// The hint line's copy, one flavor per screen family.
pub(in crate::setup::wizard::paint) enum Hint {
    /// Shortcut hints, left-aligned.
    Keys(&'static [crate::hints::Hint]),
    /// The browser sign-in's centered variant hint.
    Browser(SignInVariant),
}

impl Hint {
    /// The rows the hint takes at the panel's content `width`.
    pub fn height(&self, width: u16) -> u16 {
        match self {
            Hint::Keys(keys) => hint_line::height(keys, width).max(1),
            Hint::Browser(variant) => browser_sign_in::hint_height(*variant, width),
        }
    }

    /// The wizard's key affordances, drawn by the panel walk and by its overflow pinning alike.
    pub fn draw(&self, f: &mut Frame, area: Rect) {
        match self {
            Hint::Keys(keys) => hint_line::draw(f, area, keys),
            Hint::Browser(variant) => browser_sign_in::draw_hint(f, area, *variant),
        }
    }
}
