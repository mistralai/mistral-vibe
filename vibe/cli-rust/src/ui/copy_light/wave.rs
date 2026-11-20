//! The copy animation in cells: a soft slanted band crosses the selection's background, tinted toward the accent.

use ratatui::buffer::Buffer;

use super::gradient::Gradient;
use crate::selection::region::RowSpan;

/// How many cells the light shifts per row, so it leans like Rex's.
const ROW_SLANT: f32 = 0.7;
/// The light's soft core and wide bloom; Rex's, with a wider core so it glides in cells.
const CORE: Lobe = Lobe {
    share: 0.18,
    min_cells: 3.0,
    opacity: 0.10,
};
const BLOOM: Lobe = Lobe {
    share: 0.62,
    min_cells: 6.0,
    opacity: 0.038,
};
/// How far outside the selection the light starts and ends, as a share of its width and at least in cells.
const RUN_UP_SHARE: f32 = 0.3;
const RUN_UP_CELLS: f32 = 6.0;
/// How long the light takes to cross, starting at the copy at a steady pace.
pub const DURATION_MS: f32 = 400.0;
/// Light below which a cell keeps its own background.
const UNLIT: f32 = 0.002;
/// Samples across a cell's width, and across the time since the last frame, so the light glides instead of stepping.
const SPACE_SAMPLES: u16 = 3;
const TIME_SAMPLES: u16 = 4;

/// A part of the light: its width as a share of the selection's and at least in cells, and its opacity.
struct Lobe {
    share: f32,
    min_cells: f32,
    opacity: f32,
}

impl Lobe {
    fn width(&self, selection: f32) -> f32 {
        (self.share * selection).max(self.min_cells)
    }
}

/// The light's path along the slant and its size, both following the selection's width.
#[derive(Clone, Copy, Debug)]
pub struct Light {
    start: f32,
    length: f32,
    core: f32,
    bloom: f32,
}

impl Light {
    pub fn new(spans: &[RowSpan]) -> Self {
        let lo = spans
            .iter()
            .map(|&(y, x0, _)| slanted(f32::from(x0), f32::from(y)))
            .fold(f32::INFINITY, f32::min);
        let hi = spans
            .iter()
            .map(|&(y, _, x1)| slanted(f32::from(x1) + 1.0, f32::from(y) + 1.0))
            .fold(f32::NEG_INFINITY, f32::max);
        let width = hi - lo;
        let run_up = (RUN_UP_SHARE * width).max(RUN_UP_CELLS);
        Self {
            start: lo - run_up,
            length: width + 2.0 * run_up,
            core: CORE.width(width),
            bloom: BLOOM.width(width),
        }
    }

    /// The light's center and strength `ms` after the copy, while it runs.
    fn at(self, ms: f32) -> Option<(f32, f32)> {
        let t = ms / DURATION_MS;
        (0.0..1.0)
            .contains(&t)
            .then(|| (self.start + t * self.length, envelope(t)))
    }

    /// The light's strength from 0 to 1, `distance` cells along the slant from its center.
    fn band(self, distance: f32) -> f32 {
        let lobe = |width: f32, opacity: f32| opacity * (-(distance / width).powi(2)).exp();
        (lobe(self.core, CORE.opacity) + lobe(self.bloom, BLOOM.opacity))
            / (CORE.opacity + BLOOM.opacity)
    }

    /// The light on cell `(x, y)` `ms` after the copy, averaged over the cell's width and the `blur_ms` before.
    pub fn strength(self, x: u16, y: u16, ms: f32, blur_ms: f32) -> f32 {
        let frames = self.frames(ms, blur_ms);
        self.blurred(&frames, x, y)
    }

    fn frames(self, ms: f32, blur_ms: f32) -> Vec<(f32, f32)> {
        (0..TIME_SAMPLES)
            .filter_map(|i| self.at(ms - blur_ms * f32::from(i) / f32::from(TIME_SAMPLES)))
            .collect()
    }

    fn blurred(self, frames: &[(f32, f32)], x: u16, y: u16) -> f32 {
        let row = ROW_SLANT * (f32::from(y) + 0.5);
        let mut sum = 0.0;
        for &(center, fade) in frames {
            for j in 0..SPACE_SAMPLES {
                let u = f32::from(x) + (f32::from(j) + 0.5) / f32::from(SPACE_SAMPLES) + row;
                sum += fade * self.band(u - center);
            }
        }
        sum / f32::from(TIME_SAMPLES * SPACE_SAMPLES)
    }
}

/// Light the background of `spans` `ms` after the copy, blurring the light over `blur_ms`.
pub fn paint(buf: &mut Buffer, spans: &[RowSpan], ms: f32, blur_ms: f32, gradient: Gradient) {
    let light = Light::new(spans);
    let frames = light.frames(ms, blur_ms);
    if frames.is_empty() {
        return;
    }
    for &(y, x0, x1) in spans {
        for x in x0..=x1 {
            let lit = light.blurred(&frames, x, y);
            if let Some(cell) = buf.cell_mut((x, y)).filter(|_| lit > UNLIT) {
                cell.bg = gradient.at(lit);
            }
        }
    }
}

fn slanted(x: f32, y: f32) -> f32 {
    x + ROW_SLANT * y
}

/// The light fades in over its first fifth and out over its last.
fn envelope(t: f32) -> f32 {
    smooth(5.0 * t) * (1.0 - smooth(5.0 * (t - 0.8)))
}

fn smooth(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
}
