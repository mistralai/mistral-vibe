//! Diff sides highlighted whole, then read back one row at a time.

use ratatui::text::Span;

use crate::ui::highlight;

/// Each side highlighted whole, so multi-line strings and comments keep their state per row.
pub(super) struct Sides {
    old: Option<highlight::Highlighted>,
    new: Option<highlight::Highlighted>,
}

impl Sides {
    pub(super) fn new(old: &[&str], new: &[&str], lang: &str) -> Self {
        Self {
            old: whole(old, lang),
            new: whole(new, lang),
        }
    }

    /// The highlighted spans of 1-based `lineno` on the side a `prefix` row reads from.
    pub(super) fn line(&self, prefix: char, lineno: usize) -> Option<Vec<Span<'static>>> {
        let side = if prefix == '-' { &self.old } else { &self.new };
        side.as_ref()?.get(lineno.checked_sub(1)?).cloned()
    }
}

fn whole(lines: &[&str], lang: &str) -> Option<highlight::Highlighted> {
    highlight::code(&lines.join("\n").replace('\t', " "), lang)
}
