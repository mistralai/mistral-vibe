//! Recording indicator glyphs (Python `RecordingIndicator`).

use crate::app::App;
use crate::voice::TranscribeState;

/// Mic-level glyphs polled from `peak` while recording.
pub const PEAK_BLOCKS: [char; 8] = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
/// Cycling fill glyphs animated while flushing/transcribing.
pub const FILL_BLOCKS: [char; 8] = ['▏', '▎', '▍', '▌', '▋', '▊', '▉', '█'];

/// Single-cell glyph shown in place of the `>` marker, or `None` when idle.
pub fn glyph(app: &App) -> Option<char> {
    match app.voice.transcribe_state {
        TranscribeState::Recording => {
            let scaled = app.current_peak() * PEAK_BLOCKS.len() as f32;
            let index = (scaled as usize).min(PEAK_BLOCKS.len() - 1);
            Some(PEAK_BLOCKS[index])
        }
        TranscribeState::Flushing => Some(FILL_BLOCKS[app.voice.frame % FILL_BLOCKS.len()]),
        TranscribeState::Idle => None,
    }
}
