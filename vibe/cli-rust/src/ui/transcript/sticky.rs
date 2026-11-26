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
/// Below this many transcript rows the pinned prompt leaves out the steer under it.
pub const MIN_STEER_HEIGHT: u16 = 24;
/// Rows of a pin showing a steer: the prompt's row, the steer's row and the separator.
const STEER_PIN_ROWS: u16 = 3;

/// The pinned prompt's entry, the last steer scrolled past under it, and the viewport row the next section's prompt starts on.
#[derive(Debug, PartialEq)]
pub struct Pin {
    pub index: usize,
    pub steer: Option<usize>,
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
    // A steer opens with one gap row unless packed; it pins once its text slides under the pin.
    let gap = |at: usize| {
        let packed = transcript.settled_prompt(layout.entries[at].index);
        u16::from(!packed.is_some_and(|(_, packed, _)| packed))
    };
    let steered = layout
        .steers
        .partition_point(|&s| row(s).saturating_add(gap(s)) < position + STEER_PIN_ROWS);
    let steer = (layout.steers[..steered].last())
        .filter(|&&s| s > at && height >= MIN_STEER_HEIGHT)
        .map(|&s| layout.entries[s].index);
    Some(Pin {
        index: entry.index,
        steer,
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

/// The pinned prompt at a document row: its pin, its rows, and how many of them show.
pub(super) type Measured = (Pin, LinkedLines, u16);

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
    let steer = pin.steer.and_then(|at| transcript.settled_prompt(at));
    let steer = steer.map(|(steer, _, _)| steer);
    let lines = entry::sticky(message, fired_loop, steer, width, MAX_ROWS);
    // The next section's prompt pushes the pinned one up and out, like a CSS sticky header.
    let tall = lines.len() as u16;
    let shown = pin.next.map_or(tall, |next| next.min(tall));
    (shown > 0).then_some((pin, lines, shown))
}

/// The scroll that brings entry `index` to the viewport top, under the prompt pinned there.
pub(super) fn scroll_to(geometry: &Geometry<'_>, index: usize, total: u16) -> Option<u16> {
    let entry = geometry.layout.entries.iter().find(|e| e.index == index)?;
    let row = geometry.top.saturating_add(entry.top);
    // The closest viewport top whose pin leaves the entry uncovered; a prompt pushes the previous one out, so it needs none.
    let rows = MAX_ROWS as u16 + 1;
    let covered = |landing: u16| landing.saturating_add(inset(geometry, landing)) > row;
    let landing = (0..=rows)
        .map(|up| row.saturating_sub(up))
        .find(|&landing| !covered(landing))
        .unwrap_or(row.saturating_sub(rows));
    Some(
        total
            .saturating_sub(geometry.height)
            .saturating_sub(landing),
    )
}

/// Viewport rows the pinned prompt covers at document row `position`.
pub(super) fn inset(geometry: &Geometry<'_>, position: u16) -> u16 {
    measure(geometry, position).map_or(0, |(_, _, shown)| shown)
}

/// Paint the `measured` pinned prompt over the top of `area`.
pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect, measured: Option<Measured>) {
    app.view.sticky_prompt = None;
    app.view.sticky_steer = None;
    let Some(rect) = paint(f, area, measured.as_ref()) else {
        return;
    };
    let Some((pin, lines, shown)) = measured else {
        return;
    };
    app.view.sticky_prompt = Some((pin.index, shown));
    // The steer pins on the row above the separator, unless the next prompt pushed it out.
    let hidden = lines.len() - shown as usize;
    let at = lines.len().saturating_sub(2);
    let steer_row = (at >= hidden).then(|| rect.y + (at - hidden) as u16);
    app.view.sticky_steer = pin.steer.zip(steer_row);
    let chrome = (rect.y..rect.bottom()).map(|y| (y, rect.x, rect.right() - 1));
    app.view.selection_chrome.extend(chrome);
    mouse::register_region(app, rect, MouseTarget::StickyPrompt);
}

/// Paint the `measured` pinned prompt over the top of `area`; the rows it covers come back.
pub(super) fn paint(f: &mut Frame, area: Rect, measured: Option<&Measured>) -> Option<Rect> {
    let (_, lines, shown) = measured?;
    let rect = Rect {
        height: *shown,
        ..area
    };
    f.render_widget(Clear, rect);
    let buffer = f.buffer_mut();
    buffer.set_style(rect, theme::screen_style());
    let hidden = lines.len() - *shown as usize;
    for (row, line) in lines.lines().iter().skip(hidden).enumerate() {
        buffer.set_line(rect.x, rect.y + row as u16, line, rect.width);
    }
    Some(rect)
}
