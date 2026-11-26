//! The copy light's gradient: the selection color moved a small, fixed step away from its text, tinted toward the accent.

use ratatui::style::Color;

use crate::ui::theme::oklab::{in_gamut, luminance, oklab_of};
use crate::ui::theme::{blend, channels, primary, selection_bg, selection_fg};

/// The OKLab lightness step the peak takes away from the text: darker under light text, lighter under dark text.
const STEP: f32 = 0.06;
/// How far the peak's hue moves toward the accent, and the most the whole step may span in OKLab, so it stays sober.
const HUE: f32 = 0.3;
const MAX_STEP: f32 = 0.08;

/// The gradient's ends; the peak differs most from the selection, so the light reads as one band.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Gradient {
    pub selection: Color,
    pub light: Color,
}

impl Gradient {
    /// The gradient for `selection`, tinted toward `accent` and moved away from `text` when they are known.
    pub fn new(selection: [u8; 3], accent: Option<[u8; 3]>, text: Option<[u8; 3]>) -> Self {
        let [l, a, b] = oklab_of(selection);
        let away = if text.is_some_and(|text| luminance(text) > luminance(selection)) {
            -1.0
        } else {
            1.0
        };
        let (mut da, mut db) = accent.map_or((0.0, 0.0), |accent| {
            let [_, accent_a, accent_b] = oklab_of(accent);
            (HUE * (accent_a - a), HUE * (accent_b - b))
        });
        let room = (MAX_STEP * MAX_STEP - STEP * STEP).sqrt();
        let hue_step = da.hypot(db);
        if hue_step > room {
            (da, db) = (da * room / hue_step, db * room / hue_step);
        }
        let lightness = (l + away * STEP).clamp(0.0, 1.0);
        Self {
            selection: Color::from(selection),
            light: Color::from(in_gamut([lightness, a + da, b + db])),
        }
    }

    /// The color `k` of the way to the peak, from 0 to 1.
    pub fn at(self, k: f32) -> Color {
        blend(self.selection, self.light, k)
    }
}

/// The active theme's gradient; `None` when its selection color is unknown.
pub fn gradient() -> Option<Gradient> {
    Some(Gradient::new(
        channels(selection_bg())?,
        channels(primary()),
        channels(selection_fg()),
    ))
}
