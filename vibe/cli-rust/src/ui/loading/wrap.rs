//! The loading line in rows: it breaks only before a hint or a trailing note, so a key never parts from its action.

use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

/// Where a row may start: a ` · ` separator, an opening ` · ` or ` typing` note.
fn breaks_before(span: &Span) -> bool {
    span.content.starts_with(" · ") || span.content.starts_with(" typing")
}

/// The spans laid out in rows of at most `width` columns, breaking only before a separator.
pub(super) fn rows(spans: Vec<Span<'static>>, width: u16) -> Vec<Line<'static>> {
    let mut units: Vec<Vec<Span<'static>>> = vec![Vec::new()];
    for span in spans {
        if breaks_before(&span) {
            units.push(Vec::new());
        }
        if let Some(unit) = units.last_mut() {
            unit.push(span);
        }
    }
    let wide = |unit: &[Span]| -> usize { unit.iter().map(|span| span.content.width()).sum() };
    let mut rows: Vec<Vec<Span<'static>>> = Vec::new();
    let mut used = 0;
    for mut unit in units.into_iter().filter(|unit| !unit.is_empty()) {
        match rows.last_mut() {
            Some(row) if used + wide(&unit) <= usize::from(width) => {
                used += wide(&unit);
                row.extend(unit);
            }
            Some(_) => {
                trim_separator(&mut unit);
                used = wide(&unit);
                rows.push(unit);
            }
            None => {
                used = wide(&unit);
                rows.push(unit);
            }
        }
    }
    rows.into_iter().map(Line::from).collect()
}

#[cfg(test)]
mod tests;

/// A row opened by a ` · ` separator drops it, the way a wrapped footer drops its gap.
fn trim_separator(unit: &mut [Span<'static>]) {
    if let Some(first) = unit.first_mut() {
        let text = first
            .content
            .trim_start()
            .trim_start_matches('·')
            .trim_start();
        first.content = text.to_owned().into();
    }
}
