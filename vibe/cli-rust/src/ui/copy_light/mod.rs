//! The copy animation: a soft band crosses the copied selection's background, tinted toward the accent.

pub mod gradient;
pub mod wave;

use ratatui::buffer::Buffer;

use crate::clock::{self, UiInstant};
use crate::selection::region::RowSpan;

/// The animation's frame period, the redraw rate; the light's motion blur spans each frame.
pub const FRAME: std::time::Duration = crate::event_loop::REDRAW_INTERVAL;
/// The longest gap between frames the blur spans, so frames dropped under load streak instead of jumping.
const MAX_BLUR_FRAMES: f32 = 3.0;

/// The animation over the copied selection, `None` while idle.
#[derive(Default)]
pub struct CopyLight(Option<Running>);

#[derive(Clone, Copy)]
struct Running {
    /// When the copy happened.
    started: UiInstant,
    /// The frame to draw, in ms since the copy.
    shown_ms: f32,
    /// How long the light is blurred over: the gap since the previous frame, in ms.
    blur_ms: f32,
}

impl CopyLight {
    /// Animate the selection just copied.
    pub fn start(&mut self) {
        self.0 = Some(Running {
            started: clock::now(),
            shown_ms: 0.0,
            blur_ms: frame_ms(),
        });
    }

    /// Stop the animation: a new press replaces the copied selection.
    pub fn stop(&mut self) {
        self.0 = None;
    }

    /// Whether the animation still owes a frame.
    pub fn active(&self) -> bool {
        self.0.is_some()
    }

    /// Advance to the frame about to be drawn; once the animation is over the next frame draws the selection plain.
    pub fn step(&mut self) {
        let Some(running) = &mut self.0 else {
            return;
        };
        let ms = clock::since(running.started).as_secs_f32() * 1000.0;
        if ms >= wave::DURATION_MS {
            self.0 = None;
            return;
        }
        running.blur_ms = (ms - running.shown_ms).clamp(frame_ms(), MAX_BLUR_FRAMES * frame_ms());
        running.shown_ms = ms;
    }

    /// Animate the selection rows `spans` just painted with the frame `step` prepared.
    pub fn paint(&self, buf: &mut Buffer, spans: &[RowSpan]) {
        let (Some(running), Some(gradient)) = (self.0, gradient::gradient()) else {
            return;
        };
        wave::paint(buf, spans, running.shown_ms, running.blur_ms, gradient);
    }
}

fn frame_ms() -> f32 {
    FRAME.as_secs_f32() * 1000.0
}
