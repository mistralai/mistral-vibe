//! Shared 100 ms pulse used by in-progress transcript indicators.

pub const TICK_INTERVAL: std::time::Duration = std::time::Duration::from_millis(100);

const FRAMES: [char; 10] = ['■', '■', '■', '■', '■', '■', '□', '□', '□', '□'];

pub fn glyph(frame: usize) -> char {
    FRAMES[frame % FRAMES.len()]
}
