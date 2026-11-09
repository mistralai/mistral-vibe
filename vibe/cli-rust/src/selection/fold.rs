//! Soft-wrap folds: rows that continue the line above, so a copy rejoins them.

use ratatui::buffer::Buffer;
use unicode_width::UnicodeWidthStr;

use crate::selection::region::RowSpan;

/// A painted row that continues the logical line above it instead of opening one.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Fold {
    /// Spaces the wrap swallowed between the previous row's last glyph and this row.
    pub gap: u16,
    /// Leading cells this row paints as wrap indentation rather than content.
    pub hang: u16,
    /// The row opens its own line: only its hang is dropped, nothing rejoins.
    pub breaks: bool,
}

impl Fold {
    pub fn hung(gap: Option<u16>, hang: u16) -> Option<Self> {
        gap.map(|gap| Self {
            gap,
            hang,
            breaks: false,
        })
    }

    /// A row opening its own line whose leading `hang` cells are indentation, not content.
    pub fn indented(hang: u16) -> Self {
        Self {
            gap: 0,
            hang,
            breaks: true,
        }
    }
}

/// The sorted `(y, fold)` rows of one surface whose lines start at column `x`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Folds {
    pub x: u16,
    pub rows: Vec<(u16, Fold)>,
}

impl Folds {
    pub fn at(&self, y: u16) -> Option<Fold> {
        let index = self.rows.binary_search_by_key(&y, |(row, _)| *row).ok()?;
        Some(self.rows[index].1)
    }
}

/// How each row cut from `source` joins the one above: `None` opens a line, `Some(gap)` folds.
pub fn gaps<S: AsRef<str>>(source: &str, rows: &[S]) -> Vec<Option<u16>> {
    let mut rest = Some(source);
    rows.iter()
        .enumerate()
        .map(|(index, row)| {
            let (gap, after) = align(rest?, row.as_ref().trim_end(), index == 0);
            rest = after;
            gap
        })
        .collect()
}

/// Pair each row a wrapper cut from `source` with its fold gap, reading rows through `text`.
pub fn paired<R>(source: &str, rows: Vec<R>, text: impl Fn(&R) -> String) -> Vec<(R, Option<u16>)> {
    let texts: Vec<String> = rows.iter().map(text).collect();
    let gaps = gaps(source, &texts);
    rows.into_iter().zip(gaps).collect()
}

/// [`crate::utils::text::wrap_hard`], pairing each row with its fold gap.
pub fn wrap_hard(text: &str, width: usize) -> Vec<(String, Option<u16>)> {
    let rows = crate::utils::text::wrap_hard(text, width);
    let gaps = gaps(text, &rows);
    rows.into_iter().zip(gaps).collect()
}

/// Match `row` after the whitespace opening `rest`, returning its fold and what follows.
fn align<'a>(rest: &'a str, row: &str, first: bool) -> (Option<u16>, Option<&'a str>) {
    let (mut rest, mut hard) = (rest, first);
    let blank = rest.len() - rest.trim_start().len();
    if let Some(newline) = rest[..blank].find('\n').filter(|_| !first) {
        rest = &rest[newline + 1..];
        hard = true;
    }
    let blank = rest.len() - rest.trim_start().len();
    let start = rest[..blank]
        .char_indices()
        .map(|(at, _)| at)
        .chain(std::iter::once(blank))
        .find(|&at| rest[at..].starts_with(row));
    let Some(start) = start else {
        return (None, None);
    };
    let gap = u16::try_from(rest[..start].chars().count()).unwrap_or(u16::MAX);
    ((!hard).then_some(gap), Some(&rest[start + row.len()..]))
}

/// Read the selected cells back, rejoining folded rows without their hang indent.
pub fn extract(buf: &Buffer, spans: &[RowSpan], folds: &Folds) -> String {
    let mut text = String::new();
    let mut previous: Option<u16> = None;
    for &(y, x0, x1) in spans {
        let fold = folds.at(y);
        let x0 = fold.map_or(x0, |fold| x0.max(folds.x.saturating_add(fold.hang)));
        let row = cells(buf, x0, x1, y);
        let row = row.trim_end();
        match (previous, fold) {
            (None, _) => {}
            (Some(above), Some(fold)) if !fold.breaks && above.checked_add(1) == Some(y) => {
                if !row.is_empty() {
                    text.extend(std::iter::repeat_n(' ', usize::from(fold.gap)));
                }
            }
            (Some(_), _) => text.push('\n'),
        }
        text.push_str(row);
        previous = Some(y);
    }
    text
}

/// The glyphs painted in `[x0, x1]` on row `y`, skipping the cells a wide glyph covers.
pub fn cells(buf: &Buffer, x0: u16, x1: u16, y: u16) -> String {
    let mut row = String::new();
    let mut x = x0;
    while x <= x1 {
        let Some(cell) = buf.cell((x, y)) else {
            break;
        };
        row.push_str(cell.symbol());
        let Some(next) = x.checked_add(cell.symbol().width().max(1) as u16) else {
            break;
        };
        x = next;
    }
    row
}
