//! One `/pr` row: state dot, number, title and colored state.

use ratatui::style::Color;
use ratatui::text::Span;
use unicode_width::UnicodeWidthStr;

use super::{list_cursor, theme};
use crate::artifacts;
use crate::server::{PrState, PublicSessionArtifact};
use crate::utils::text::ellipsize;

const DOT: &str = "● ";
const GAP: usize = 2;
/// The widest state word (`merged`, `closed`).
const STATE_WIDTH: usize = 6;

/// The row's state: an unsynced or unknown state reads as open.
pub fn state(pr: &PublicSessionArtifact) -> PrState {
    match artifacts::state(pr) {
        Some(PrState::Unknown) | None => PrState::Open,
        Some(state) => state,
    }
}

pub fn word(state: PrState) -> &'static str {
    match state {
        PrState::Open | PrState::Unknown => "open",
        PrState::Draft => "draft",
        PrState::Merged => "merged",
        PrState::Closed => "closed",
    }
}

/// GitHub's colors: open green, draft grey, merged purple, closed red.
pub fn color(state: PrState) -> Color {
    match state {
        PrState::Open | PrState::Unknown => theme::text_success(),
        PrState::Draft => theme::muted(),
        PrState::Merged => theme::merged(),
        PrState::Closed => theme::text_error(),
    }
}

/// The number and title column widths shared by every row.
#[derive(Clone, Copy, Debug)]
pub struct Columns {
    number: usize,
    title: usize,
}

impl Columns {
    /// The widths fitting every row of `prs` in `max` cells, the title shrinking first.
    pub fn fit(prs: &[&PublicSessionArtifact], max: usize) -> Self {
        let widest = |text: fn(&PublicSessionArtifact) -> String| {
            prs.iter().map(|pr| text(pr).width()).max().unwrap_or(0)
        };
        let number = widest(artifacts::short_label);
        let fixed = DOT.width() + number + 2 * GAP + STATE_WIDTH;
        let title = widest(title).min(max.saturating_sub(fixed));
        Self { number, title }
    }
}

fn title(pr: &PublicSessionArtifact) -> String {
    pr.display_title.clone().unwrap_or_default()
}

fn pad(text: String, width: usize) -> String {
    let fill = width.saturating_sub(text.width());
    format!("{text}{}", " ".repeat(fill))
}

/// `● #124  Rework the footer  open`, on the list cursor bar when highlighted.
pub fn spans(
    pr: &PublicSessionArtifact,
    columns: Columns,
    highlighted: bool,
) -> Vec<Span<'static>> {
    let (base, dim) = list_cursor::styles(highlighted);
    let state = state(pr);
    let title = ellipsize(&title(pr), columns.title);
    vec![
        // The dot keeps its color on the cursor bar too, so the highlighted row still shows its state.
        Span::styled(DOT, base.fg(color(state))),
        Span::styled(pad(artifacts::short_label(pr), columns.number + GAP), dim),
        Span::styled(pad(title, columns.title + GAP), base),
        Span::styled(word(state), list_cursor::accent(base, color(state))),
    ]
}
