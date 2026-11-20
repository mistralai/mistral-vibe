//! Per-row lookups into the transcript's row-range hit maps (entry chrome, `/raw` messages).

/// `(top, bottom, indent, border, gutter)`: rows, `Worked for` rail, border and edit-diff gutter columns.
pub type RowChrome = (u16, u16, u16, u16, u16);

/// The `(indent, border, gutter)` chrome columns of screen row `y`, or zeros outside a bordered entry.
pub(super) fn chrome_at(chrome: &[RowChrome], y: u16) -> (u16, u16, u16) {
    chrome
        .iter()
        .find(|(top, bottom, ..)| y >= *top && y < *bottom)
        .map_or((0, 0, 0), |&(_, _, indent, border, gutter)| {
            (indent, border, gutter)
        })
}

/// Whether row `y` belongs to a `/raw` message, whose every painted cell is text.
pub(super) fn in_raw(raw: &[(u16, u16)], y: u16) -> bool {
    raw.iter().any(|&(top, bottom)| y >= top && y < bottom)
}
