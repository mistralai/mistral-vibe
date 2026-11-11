//! The prompt of the section at the viewport top, pinned over the transcript once it scrolls off.

use ratatui::layout::Rect;
use ratatui::widgets::Clear;
use ratatui::Frame;

use super::entry;
use crate::app::App;
use crate::mouse::{self, MouseTarget};
use crate::transcript::Transcript;
use crate::ui::markdown::LinkedLines;
use crate::ui::theme;
use crate::utils::transcript_cache::TranscriptLayout;

/// Below this many transcript rows the conversation keeps every row.
pub const MIN_HEIGHT: u16 = 15;
/// The pinned prompt shows at most this many text rows, the last one ellipsized.
pub const MAX_ROWS: usize = 3;

/// The pinned prompt's entry, and the viewport row the next section's prompt starts on.
#[derive(Debug, PartialEq)]
pub struct Pin {
    pub index: usize,
    pub next: Option<u16>,
}

/// The prompt opening the section at document row `position`, once its text has scrolled above it.
pub fn pin(
    layout: &TranscriptLayout,
    transcript: &Transcript,
    top: u16,
    position: u16,
    height: u16,
) -> Option<Pin> {
    let row = |at: usize| top.saturating_add(layout.entries[at].top);
    let above = layout.prompts.partition_point(|&at| row(at) < position);
    let at = *layout.prompts[..above].last()?;
    let entry = &layout.entries[at];
    let (_, packed, _) = transcript.settled_prompt(entry.index)?;
    // A prompt opens with two gap rows unless it is packed against the previous one.
    if row(at).saturating_add(if packed { 0 } else { 2 }) >= position {
        return None;
    }
    let next = layout
        .prompts
        .get(above)
        .map(|&next| row(next) - position)
        .filter(|&next| next < height);
    Some(Pin {
        index: entry.index,
        next,
    })
}

/// What the pinned prompt is measured against: the transcript at content width.
#[derive(Clone, Copy)]
pub(super) struct Geometry<'a> {
    pub layout: &'a TranscriptLayout,
    pub transcript: &'a Transcript,
    /// Document row of the first entry.
    pub top: u16,
    pub height: u16,
    pub width: u16,
    /// The `enable_sticky_prompt` config value.
    pub enabled: bool,
}

/// The pinned prompt at a document row: its entry, its rows, and how many of them show.
pub(super) type Measured = (usize, LinkedLines, u16);

/// The pinned prompt at document row `position`.
pub(super) fn measure(geometry: &Geometry<'_>, position: u16) -> Option<Measured> {
    let Geometry {
        layout,
        transcript,
        top,
        height,
        width,
        enabled,
    } = *geometry;
    if !enabled || height < MIN_HEIGHT || width == 0 {
        return None;
    }
    let pin = pin(layout, transcript, top, position, height)?;
    let (message, _, fired_loop) = transcript.settled_prompt(pin.index)?;
    let lines = entry::sticky(message, fired_loop, width, MAX_ROWS);
    // The next section's prompt pushes the pinned one up and out, like a CSS sticky header.
    let tall = lines.len() as u16;
    let shown = pin.next.map_or(tall, |next| next.min(tall));
    (shown > 0).then_some((pin.index, lines, shown))
}

/// Viewport rows the pinned prompt covers at document row `position`.
pub(super) fn inset(geometry: &Geometry<'_>, position: u16) -> u16 {
    measure(geometry, position).map_or(0, |(_, _, shown)| shown)
}

/// Paint the `measured` pinned prompt over the top of `area`.
pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect, measured: Option<Measured>) {
    app.view.sticky_prompt = None;
    let Some((index, lines, shown)) = measured else {
        return;
    };
    let rect = Rect {
        height: shown,
        ..area
    };
    f.render_widget(Clear, rect);
    let buffer = f.buffer_mut();
    buffer.set_style(rect, theme::screen_style());
    let hidden = lines.len() - shown as usize;
    for (row, line) in lines.lines().iter().skip(hidden).enumerate() {
        buffer.set_line(rect.x, rect.y + row as u16, line, rect.width);
    }
    app.view.sticky_prompt = Some((index, shown));
    let chrome = (rect.y..rect.bottom()).map(|y| (y, rect.x, rect.right() - 1));
    app.view.selection_chrome.extend(chrome);
    mouse::register_region(app, rect, MouseTarget::StickyPrompt);
}
