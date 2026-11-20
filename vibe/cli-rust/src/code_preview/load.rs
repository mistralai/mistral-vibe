//! Read a linked file off the main thread and highlight the lines around its focus.

use ratatui::text::Span;

use super::target::Location;
use crate::ui::highlight;
use crate::utils::local_file::Unread;

const MAX_FILE_BYTES: u64 = 4 * 1024 * 1024;
/// Lines kept above the focus, and in total, so the wheel can browse around it.
const BEFORE: usize = 100;
const WINDOW: usize = 400;
/// Lines above the window highlighted with it, so a string or comment opened there still colors it.
const LOOKBACK: usize = 1000;
const MAX_LINE_CHARS: usize = 500;
const TAB: &str = "    ";

pub struct Snippet {
    /// The path, relative to the session directory when inside it, and the linked lines.
    pub title: String,
    pub focus: Option<(usize, usize)>,
    pub lines: Vec<Vec<Span<'static>>>,
    /// The one-based file line of the first entry of `lines`.
    pub first: usize,
    /// Lines in the whole file.
    pub total: usize,
}

impl Snippet {
    /// Index of the row showing file line `line`.
    pub fn row_of(&self, line: usize) -> Option<usize> {
        line.checked_sub(self.first)
            .filter(|&row| row < self.lines.len())
    }
}

/// Why a linked file shows no preview.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Failure {
    NotFound,
    NotFile,
    TooLarge,
    Unreadable,
    Binary,
    Empty,
}

impl Failure {
    /// The `error_type` telemetry reports.
    pub fn name(self) -> &'static str {
        match self {
            Self::NotFound => "not_found",
            Self::NotFile => "not_file",
            Self::TooLarge => "too_large",
            Self::Unreadable => "unreadable",
            Self::Binary => "binary",
            Self::Empty => "empty",
        }
    }
}

impl From<Unread> for Failure {
    fn from(unread: Unread) -> Self {
        match unread {
            Unread::NotFound => Self::NotFound,
            Unread::NotFile => Self::NotFile,
            Unread::TooLarge => Self::TooLarge,
            Unread::Unreadable => Self::Unreadable,
        }
    }
}

/// The lines around the link's focus, or why the file has none to show.
pub fn read(location: &Location, cwd: Option<&str>) -> Result<Snippet, Failure> {
    let bytes = crate::utils::local_file::read_bounded(&location.path, MAX_FILE_BYTES)?;
    if bytes.contains(&0) {
        return Err(Failure::Binary);
    }
    let text = String::from_utf8_lossy(&bytes);
    let all: Vec<&str> = text.lines().collect();
    let last = all.len().max(1);
    let focus = location
        .lines
        .map(|(start, end)| (start.min(last), end.min(last)));
    let start = focus
        .map_or(0, |(line, _)| line.saturating_sub(1))
        .saturating_sub(BEFORE);
    let end = (start + WINDOW).min(all.len());
    if start >= end {
        return Err(Failure::Empty);
    }
    let base = start.saturating_sub(LOOKBACK);
    let texts: Vec<String> = all[base..end].iter().map(|line| clean(line)).collect();
    let lang = location.path.extension().and_then(|ext| ext.to_str());
    Ok(Snippet {
        title: title(location, cwd),
        focus,
        lines: highlight_window(&texts, lang.unwrap_or(""), start - base),
        first: start + 1,
        total: last,
    })
}

/// The rows from `from`, highlighted from the first row so an open string or comment carries in; the window alone when the whole is too large.
fn highlight_window(texts: &[String], lang: &str, from: usize) -> Vec<Vec<Span<'static>>> {
    let mut lines: Vec<_> = texts[from..]
        .iter()
        .map(|text| vec![Span::raw(text.clone())])
        .collect();
    let block = |first: usize| highlight::block(&texts[first..].join("\n"), lang);
    let (first, highlighted) = match block(0) {
        Some(highlighted) => (0, highlighted),
        None => (from, block(from).unwrap_or_default()),
    };
    for (row, spans) in (first..).zip(highlighted) {
        if let Some(line) = row.checked_sub(from).and_then(|row| lines.get_mut(row)) {
            *line = spans;
        }
    }
    lines
}

/// Expand tabs, blank control characters and cap the line length.
fn clean(line: &str) -> String {
    line.replace('\t', TAB)
        .chars()
        .take(MAX_LINE_CHARS)
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect()
}

fn title(location: &Location, cwd: Option<&str>) -> String {
    let path = cwd
        .and_then(|cwd| location.path.strip_prefix(cwd).ok())
        .unwrap_or(&location.path)
        .display()
        .to_string();
    match location.lines {
        Some((start, end)) if start != end => format!("{path}:{start}-{end}"),
        Some((start, _)) => format!("{path}:{start}"),
        None => path,
    }
}
