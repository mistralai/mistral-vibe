//! Paint the region selection highlight and copy on a pending release.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::Frame;

use super::{notice, theme};
use crate::app::App;
use crate::selection::region::{self, RegionId, RowSpan};

/// Highlight the selection inside the main screen region.
pub fn overlay(app: &mut App, f: &mut Frame) {
    overlay_region(app, f, RegionId::Main);
}

/// Highlight the selection `owner` published, if it is the one in flight.
pub fn overlay_region(app: &mut App, f: &mut Frame, owner: RegionId) {
    if app
        .selection
        .region
        .as_ref()
        .is_none_or(|selection| selection.owner != owner)
    {
        return;
    }
    // The scrollbar gutter is chrome; selection stops one column short of it.
    let content_chat = region::get(app, owner).content();
    let spans = region::spans(app, f.buffer_mut(), content_chat);
    if !cache_and_copy(app, f, &spans) {
        return;
    }
    let style = Style::default()
        .fg(theme::selection_fg())
        .bg(theme::selection_bg());
    let buf = f.buffer_mut();
    for &span in &spans {
        paint_row(buf, content_chat, style, span);
    }
}

/// On the finalized (pending) frame, extract the selected text once, cache it so
/// a copy key can read it with autocopy off, and copy it. Extraction re-renders
/// the transcript slice, so it must not run on every drag frame or spinner tick.
/// Returns false once a pending release turned out empty and cleared the selection.
fn cache_and_copy(app: &mut App, f: &mut Frame, spans: &[RowSpan]) -> bool {
    if !app
        .selection
        .region
        .as_ref()
        .is_some_and(|sel| sel.pending_copy)
    {
        return true;
    }
    let text =
        region::extract_document(app).unwrap_or_else(|| region::extract(f.buffer_mut(), spans));
    if text.is_empty() {
        app.selection.region = None;
        return false;
    }
    if let Some(selection) = app.selection.region.as_mut() {
        selection.text = text.clone();
        selection.pending_copy = false;
    }
    notice::copy_if_autocopy(app, &text);
    true
}

/// Highlight one row. A folded group header composites its label's
/// `text-opacity: 55%` over the selection colors (Textual `TextOpacity`),
/// while its marker indicator repaints with the raw selection style.
fn paint_row(buf: &mut Buffer, chat: Rect, style: Style, (y, x0, x1): RowSpan) {
    if is_group_header(buf, chat, y) {
        let label = group_label_style();
        for x in x0..=x1 {
            let Some(cell) = buf.cell_mut((x, y)) else {
                continue;
            };
            // The marker cell and its trailing pad sit before the label.
            let patch = if x < chat.x + 2 { style } else { label };
            cell.set_style(cell.style().remove_modifier(Modifier::all()).patch(patch));
        }
        return;
    }
    for x in x0..=x1 {
        if let Some(cell) = buf.cell_mut((x, y)) {
            cell.set_style(cell.style().patch(style));
        }
    }
}

/// A folded group header row: its status indicator glyph at the row's first cell.
fn is_group_header(buf: &Buffer, chat: Rect, y: u16) -> bool {
    buf.cell((chat.x, y))
        .is_some_and(|cell| matches!(cell.symbol(), "⏵" | "⏷" | "■" | "□"))
}

/// The selected group-header label: the label's text-opacity blends its color
/// toward the selection background, with ANSI themes resolving the selection
/// colors through Textual's `ansi_theme_dark` (MONOKAI) or `ansi_theme_light`
/// (ALABASTER) palette and folding the label's dim in (Python
/// `TextOpacity.process_segments` over `ANSIToTruecolor`).
fn group_label_style() -> Style {
    const MONOKAI_BRIGHT_BLUE: Color = Color::Rgb(157, 101, 255);
    const MONOKAI_BLACK: Color = Color::Rgb(26, 26, 26);
    const ALABASTER_CYAN: Color = Color::Rgb(0, 131, 178);
    const ALABASTER_BRIGHT_WHITE: Color = Color::Rgb(247, 247, 247);
    /// Textual `DIM_FACTOR` / the header's `text-opacity`.
    const DIM_FACTOR: f32 = 0.66;
    const TEXT_OPACITY: f32 = 0.55;

    let (bg, fg) = if theme::is_ansi() {
        // The theme's raw `screen-selection-*` colors, indexed into the palette
        // Textual picks by dark vs light (`bright_blue`/`black` vs `cyan`/`bright_white`).
        let (bg, selection_fg) = if theme::is_dark() {
            (MONOKAI_BRIGHT_BLUE, MONOKAI_BLACK)
        } else {
            (ALABASTER_CYAN, ALABASTER_BRIGHT_WHITE)
        };
        let dimmed = theme::blend(bg, selection_fg, DIM_FACTOR);
        (bg, theme::blend(bg, dimmed, TEXT_OPACITY))
    } else {
        let bg = theme::selection_bg();
        (bg, theme::blend(bg, theme::selection_fg(), TEXT_OPACITY))
    };
    Style::default().fg(fg).bg(bg)
}
