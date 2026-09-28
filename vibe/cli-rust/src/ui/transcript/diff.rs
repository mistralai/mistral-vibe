//! Edit-effect diff rows, mirroring the Textual `diff_rendering` widget.

mod line_numbers;

use line_numbers::next_line_number;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Span;
use unicode_width::UnicodeWidthStr;

use super::super::{highlight, theme};
use super::difflib;

/// Python `_BAND_ALPHA_DARK` / `_BAND_ALPHA_LIGHT`: the row tint over the backdrop.
const BAND_ALPHA_DARK: f32 = 0.10;
const BAND_ALPHA_LIGHT: f32 = 0.20;
/// Context lines kept around each change (Python's `unified_diff(..., n=2)`).
const CONTEXT_LINES: usize = 2;
/// Minimum line-number width and fixed sign width in the diff gutter.
const MIN_LINENO_WIDTH: u16 = 5;
const SIGN_WIDTH: u16 = 2;

/// Python `DiffOccurrence`: one match, expanded to whole lines, anchored at `start_line`.
pub struct DiffOccurrence {
    pub start_line: Option<u32>,
    pub old_lines: String,
    pub new_lines: String,
}

/// One rendered diff row: its gutter border, its full-width band, and its text.
pub struct DiffRow {
    pub border: Style,
    pub band: Option<Color>,
    pub gutter_width: u16,
    pub spans: Vec<Span<'static>>,
}

/// Python `EditResultWidget._occurrences`: the reported occurrences, else the bare strings.
pub fn occurrences(output: &crate::server::FileEditEffectOutput) -> Vec<DiffOccurrence> {
    if output.occurrences.is_empty() {
        return vec![DiffOccurrence {
            start_line: None,
            old_lines: output.old_string.clone(),
            new_lines: output.new_string.clone(),
        }];
    }
    output
        .occurrences
        .iter()
        .map(|item| DiffOccurrence {
            start_line: item.start_line,
            old_lines: item.old_text.clone(),
            new_lines: item.new_text.clone(),
        })
        .collect()
}

/// Python `DiffView._gutter_width`: the widest row gutter, uniform per view.
/// It is chrome, so selection and copy start after it.
pub fn gutter_width(occurrences: &[DiffOccurrence]) -> u16 {
    let line_number_width = occurrences
        .iter()
        .filter_map(|item| {
            let start = item.start_line? as usize;
            let line_count = split_lines(&item.old_lines)
                .len()
                .max(split_lines(&item.new_lines).len());
            let last = start.saturating_add(line_count.saturating_sub(1));
            Some(formatted_line_number(last, MIN_LINENO_WIDTH).width() as u16)
        })
        .max()
        .unwrap_or(0);
    SIGN_WIDTH + line_number_width
}

/// Python `language_for_path`: the bare file extension, else no language.
pub fn language(file: &str) -> &str {
    file.rsplit_once('.').map(|(_, ext)| ext).unwrap_or("")
}

/// Python `render_edit_diff`: every occurrence's hunks, separated by gap rows.
pub fn render_edit_diff(occurrences: &[DiffOccurrence], lang: &str) -> Vec<DiffRow> {
    let gutter_width = gutter_width(occurrences);
    let mut rows = Vec::new();
    for (index, occurrence) in occurrences.iter().enumerate() {
        if index > 0 {
            rows.push(gap_row());
        }
        // rstrip only: a trailing newline is a phantom line, but a leading one is
        // a real empty line anchored at `start_line`.
        let old = split_lines(&occurrence.old_lines);
        let new = split_lines(&occurrence.new_lines);
        let offset = occurrence.start_line.unwrap_or(1).saturating_sub(1) as usize;
        for (index, hunk) in difflib::unified_diff(&old, &new, CONTEXT_LINES)
            .iter()
            .enumerate()
        {
            // The `@@` header is dropped (the gutter carries line numbers); a gap
            // row marks the break between hunks instead.
            if index > 0 {
                rows.push(gap_row());
            }
            rows.extend(render_hunk(
                hunk,
                occurrence.start_line.map(|_| offset),
                gutter_width,
                lang,
            ));
        }
    }
    rows
}

fn split_lines(text: &str) -> Vec<&str> {
    text.trim_end_matches('\n').split('\n').collect()
}

/// Render one hunk's rows, numbering them from the hunk header plus `offset`.
fn render_hunk(
    hunk: &difflib::Hunk,
    offset: Option<usize>,
    gutter_width: u16,
    lang: &str,
) -> Vec<DiffRow> {
    let mut old_lineno = hunk.old_start;
    let mut new_lineno = hunk.new_start;
    let mut rows = Vec::new();
    for (prefix, code) in &hunk.rows {
        let lineno = next_line_number(*prefix, &mut old_lineno, &mut new_lineno);
        rows.push(row(
            *prefix,
            offset.map(|offset| lineno + offset),
            gutter_width,
            code,
            lang,
        ));
    }
    rows
}

fn row(prefix: char, lineno: Option<usize>, gutter_width: u16, code: &str, lang: &str) -> DiffRow {
    let band = band(prefix);
    let background = band.unwrap_or_else(theme::background);
    let (sign_style, lineno_style) = gutter_styles(prefix, background);
    let gutter_width = if lineno.is_some() {
        gutter_width
    } else {
        SIGN_WIDTH
    };
    let mut spans = Vec::new();
    if let Some(lineno) = lineno {
        spans.push(Span::styled(
            formatted_line_number(lineno, gutter_width - SIGN_WIDTH),
            lineno_style,
        ));
    }
    spans.push(Span::styled(format!("{prefix} "), sign_style));
    spans.extend(body(code, prefix, lang));
    DiffRow {
        border: border_style(prefix),
        band,
        gutter_width,
        spans,
    }
}

fn formatted_line_number(lineno: usize, width: u16) -> String {
    let width = width.saturating_sub(1) as usize;
    format!("{lineno:>width$} ")
}

/// Python `_gap_line`: the ellipsis separator between hunks and occurrences.
fn gap_row() -> DiffRow {
    let style = if theme::is_ansi() {
        theme::muted_style()
    } else {
        theme::text(theme::text_muted())
    };
    DiffRow {
        border: theme::muted_style(),
        band: None,
        gutter_width: 0,
        spans: vec![Span::styled("⋯", style)],
    }
}

/// Python `_BAND_TINT_BY_CLASS`: added/removed rows tint the whole row width.
/// ANSI themes leave the background untouched (the old `&:ansi` rule).
fn band(prefix: char) -> Option<Color> {
    if theme::is_ansi() {
        return None;
    }
    let tint = match prefix {
        '+' => theme::status_ready(),
        '-' => theme::error(),
        _ => return None,
    };
    let alpha = if theme::is_dark() {
        BAND_ALPHA_DARK
    } else {
        BAND_ALPHA_LIGHT
    };
    Some(theme::blend(theme::background(), tint, alpha))
}

/// Python `DIFF_BORDER_COLOR_BY_CLASS`: the expanding border tracks each row's class.
fn border_style(prefix: char) -> Style {
    match prefix {
        '+' => theme::text(theme::status_ready()),
        '-' => theme::text(theme::error()),
        _ => theme::muted_style(),
    }
}

/// Python `_gutter_styles`, resolved against the row's own background.
fn gutter_styles(prefix: char, background: Color) -> (Style, Style) {
    let muted = dim_muted(background);
    match (prefix, theme::is_ansi()) {
        ('-', true) => (theme::text(theme::error()), theme::text(theme::error())),
        ('+', true) => (
            theme::text(theme::status_ready()),
            theme::text(theme::status_ready()),
        ),
        ('-', false) => (theme::text(theme::text_error()), muted),
        ('+', false) => (theme::text(theme::text_success()), muted),
        (_, _) => (theme::text(theme::text_muted()), muted),
    }
}

/// `dim $text-muted`: ANSI themes use the SGR dim, truecolor blends toward the row band.
fn dim_muted(background: Color) -> Style {
    if theme::is_ansi() {
        theme::muted_style()
    } else {
        theme::text(theme::blend(
            background,
            theme::text_muted(),
            theme::DIM_FACTOR,
        ))
    }
}

/// Python `_build_diff_body`: the code, highlighted a line at a time (so no
/// parser state crosses rows, as in Textual), dimmed on removed ANSI rows.
fn body(code: &str, prefix: char, lang: &str) -> Vec<Span<'static>> {
    // A tab occupies no cell, so it would leave the row's band and the cells it
    // covers unpainted; Textual renders it as one space.
    let code = &code.replace('\t', " ");
    let dim = prefix == '-' && theme::is_ansi();
    let plain = || {
        let style = theme::text(theme::code_plain());
        vec![Span::styled(
            code.to_owned(),
            if dim {
                style.add_modifier(Modifier::DIM)
            } else {
                style
            },
        )]
    };
    let Some(rows) = highlight::code(code, lang) else {
        return plain();
    };
    let Some(spans) = rows.into_iter().next() else {
        return plain();
    };
    spans
        .into_iter()
        .map(|span| {
            if dim {
                let style = span.style.add_modifier(Modifier::DIM);
                span.style(style)
            } else {
                span
            }
        })
        .collect()
}
