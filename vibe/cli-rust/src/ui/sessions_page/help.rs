//! The Sessions page footer's marks row, and what `?` unfolds: every key, then every mark.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use super::super::{hint_line, list_cursor, theme};
use super::legend::State;
use crate::hints::{self, action, key, Hint};

const KEYS: &[Hint] = &[
    hints::NAVIGATE,
    (key::RIGHT_ENTER, action::OPEN),
    (key::LEFT, action::FOLDERS),
    ("n", action::NEW),
    ("r", action::RENAME),
    ("a", action::ARCHIVE),
    (key::SPACE, action::FOLD),
    (key::SHIFT_ARROWS, action::SCROLL_PREVIEW),
    ("/", action::SEARCH),
    ("c", action::THIS_FOLDER),
    ("s", action::SORT),
    (".", action::ARCHIVED),
    ("g", action::GONE_FOLDERS),
    COLLAPSE,
];

const COLLAPSE: Hint = ("?/Esc", action::COLLAPSE);

/// The label column every section starts with.
const LABEL_WIDTH: usize = 7;

/// One item of a section: styled runs laid out as a unit.
type Item = Vec<(String, Style)>;

/// The unfolded rows, wrapped to `width`, at most `height`: when they do not
/// all fit, the last row left says how to fold them back.
pub(super) fn lines(width: u16, height: usize) -> Vec<Line<'static>> {
    let keys: Vec<Item> = KEYS
        .iter()
        .map(|hint| hint_line::styled(&[*hint]))
        .collect();
    let mut lines = section("Keys", keys, width.into());
    lines.extend(section("Marks", marks(), width.into()));
    if lines.len() > height {
        lines.truncate(height.saturating_sub(1));
        let fold = hint_line::styled(&[COLLAPSE]);
        lines.push(Line::from(
            std::iter::once(Span::styled(
                format!("{:<LABEL_WIDTH$}", "…"),
                theme::muted_style(),
            ))
            .chain(
                fold.into_iter()
                    .map(|(text, style)| Span::styled(text, style)),
            )
            .collect::<Vec<_>>(),
        ));
    }
    lines
}

/// The folded footer's marks: as many as fit one row of `width`, the most common first.
pub(super) fn marks_line(width: u16) -> Line<'static> {
    let room = usize::from(width);
    let mut spans: Vec<Span<'static>> = Vec::new();
    let mut used = 0;
    for item in marks() {
        let gap = if spans.is_empty() { 0 } else { 2 };
        let wide: usize = item.iter().map(|(text, _)| text.width()).sum();
        if used + gap + wide > room {
            break;
        }
        if gap > 0 {
            spans.push(Span::raw("  "));
        }
        used += gap + wide;
        spans.extend(
            item.into_iter()
                .map(|(text, style)| Span::styled(text, style)),
        );
    }
    Line::from(spans)
}

/// Every row mark with what it means, the most common first.
fn marks() -> Vec<Item> {
    let mut marks: Vec<Item> = State::ALL
        .iter()
        .map(|state| {
            vec![
                (state.glyph().to_owned(), theme::text(state.color())),
                (format!(" {}", state.label()), hint_line::action_style()),
            ]
        })
        .collect();
    marks.push(vec![
        (
            list_cursor::CURRENT.trim_end().to_owned(),
            theme::text(list_cursor::current_color()),
        ),
        (" current session".to_owned(), hint_line::action_style()),
    ]);
    marks.push(vec![
        ("├".to_owned(), theme::muted_style()),
        (
            " started by the session above".to_owned(),
            hint_line::action_style(),
        ),
    ]);
    marks
}

/// `items` after a label, wrapped under it.
fn section(label: &str, items: Vec<Item>, width: usize) -> Vec<Line<'static>> {
    let room = width.saturating_sub(LABEL_WIDTH).max(1);
    let mut rows: Vec<Vec<Span<'static>>> = Vec::new();
    let mut row: Vec<Span<'static>> = Vec::new();
    let mut used = 0;
    for item in items {
        let item_width: usize = item.iter().map(|(text, _)| text.width()).sum();
        if used > 0 && used + 2 + item_width > room {
            rows.push(std::mem::take(&mut row));
            used = 0;
        } else if used > 0 {
            row.push(Span::raw("  "));
            used += 2;
        }
        used += item_width;
        row.extend(
            item.into_iter()
                .map(|(text, style)| Span::styled(text, style)),
        );
    }
    rows.push(row);
    rows.into_iter()
        .enumerate()
        .map(|(index, spans)| {
            let head = if index == 0 { label } else { "" };
            let mut line = vec![Span::styled(
                format!("{head:<LABEL_WIDTH$}"),
                theme::muted_style(),
            )];
            line.extend(spans);
            Line::from(line)
        })
        .collect()
}
