//! Per-row lookups into the transcript's row-range hit maps (diff gutters, `/raw` messages).

/// The diff gutter width covering screen row `y`, or 0 outside an edit-diff view.
pub(super) fn gutter_at(gutters: &[(u16, u16, u16)], y: u16) -> u16 {
    gutters
        .iter()
        .find(|(top, bottom, _)| y >= *top && y < *bottom)
        .map_or(0, |(_, _, gutter)| *gutter)
}

/// Whether row `y` belongs to a `/raw` message, whose every painted cell is text.
pub(super) fn in_raw(raw: &[(u16, u16)], y: u16) -> bool {
    raw.iter().any(|&(top, bottom)| y >= top && y < bottom)
}
