//! Rendered lines carrying the link runs painted on each, and their on-screen hit areas.

use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::{buffer::Buffer, layout::Rect};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::Sc;
use crate::ui::theme;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LinkKind {
    External,
    Attachment,
    /// An inline image, named by `inline_images::target`, written to a file when clicked.
    InlineImage,
}

#[derive(Clone)]
struct Target {
    url: String,
    kind: LinkKind,
}

/// `width` cells from column `x` of one line, painted for link `link`.
#[derive(Clone, Copy)]
struct Run {
    link: usize,
    x: usize,
    width: usize,
}

/// Lines and the link runs painted on each: a run moves with its line, so they never drift apart.
#[derive(Clone, Default)]
pub struct LinkedLines {
    lines: Vec<Line<'static>>,
    runs: Vec<Vec<Run>>,
    targets: Vec<Target>,
}

impl LinkedLines {
    pub fn lines(&self) -> &[Line<'static>] {
        &self.lines
    }

    pub fn into_lines(self) -> Vec<Line<'static>> {
        self.lines
    }

    pub fn len(&self) -> usize {
        self.lines.len()
    }

    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    /// Register a link target; its id tags the `Sc`s a later `push_row` paints for it.
    pub fn link(&mut self, url: String, kind: LinkKind) -> usize {
        self.targets.push(Target { url, kind });
        self.targets.len() - 1
    }

    pub fn push(&mut self, line: Line<'static>) {
        self.lines.push(line);
        self.runs.push(Vec::new());
    }

    /// Push `row` after its `lead` spans, recording the cells its linked characters land on.
    pub fn push_row(&mut self, mut lead: Vec<Span<'static>>, row: &[Sc]) {
        let mut runs: Vec<Run> = Vec::new();
        let mut x: usize = lead.iter().map(Span::width).sum();
        let text: String = row.iter().map(|sc| sc.0).collect();
        let mut at = 0;
        for grapheme in text.graphemes(true) {
            let width = grapheme.width();
            if let Some(link) = row[at].2.filter(|_| width > 0) {
                match runs.last_mut() {
                    Some(run) if run.link == link && run.x + run.width == x => run.width += width,
                    _ => runs.push(Run { link, x, width }),
                }
            }
            at += grapheme.chars().count();
            x += width;
        }
        lead.extend(super::text::merge(row));
        self.lines.push(Line::from(lead));
        self.runs.push(runs);
    }

    pub fn append(&mut self, other: LinkedLines) {
        let base = self.targets.len();
        self.lines.extend(other.lines);
        self.runs.extend(other.runs.into_iter().map(|mut runs| {
            runs.iter_mut().for_each(|run| run.link += base);
            runs
        }));
        self.targets.extend(other.targets);
    }

    /// Insert `span(index)` at the start of each line, shifting its runs by the span's width.
    pub fn prefix(&mut self, span: impl Fn(usize) -> Span<'static>) {
        for (index, (line, runs)) in self.lines.iter_mut().zip(&mut self.runs).enumerate() {
            let span = span(index);
            let width = span.width();
            line.spans.insert(0, span);
            runs.iter_mut().for_each(|run| run.x += width);
        }
    }

    /// Keep only the lines `keep(index, line)` accepts, with their runs.
    pub fn retain(&mut self, mut keep: impl FnMut(usize, &Line<'static>) -> bool) {
        let lines = std::mem::take(&mut self.lines);
        let runs = std::mem::take(&mut self.runs);
        (self.lines, self.runs) = lines
            .into_iter()
            .zip(runs)
            .enumerate()
            .filter(|(index, (line, _))| keep(*index, line))
            .map(|(_, pair)| pair)
            .unzip();
    }

    pub(super) fn retained_bytes(&self) -> usize {
        self.lines.capacity() * std::mem::size_of::<Line<'static>>()
            + self.runs.capacity() * std::mem::size_of::<Vec<Run>>()
            + self
                .runs
                .iter()
                .map(|runs| runs.capacity() * std::mem::size_of::<Run>())
                .sum::<usize>()
            + self.targets.capacity() * std::mem::size_of::<Target>()
            + self
                .targets
                .iter()
                .map(|target| target.url.capacity())
                .sum::<usize>()
    }
}

impl Extend<Line<'static>> for LinkedLines {
    fn extend<I: IntoIterator<Item = Line<'static>>>(&mut self, lines: I) {
        lines.into_iter().for_each(|line| self.push(line));
    }
}

impl From<Vec<Line<'static>>> for LinkedLines {
    fn from(lines: Vec<Line<'static>>) -> Self {
        let runs = std::iter::repeat_with(Vec::new).take(lines.len()).collect();
        Self {
            lines,
            runs,
            targets: Vec::new(),
        }
    }
}

#[derive(Clone)]
pub struct Link {
    target: String,
    kind: LinkKind,
    /// Painted spans as `(y, x, width)`, one per screen row the label covers.
    rows: Vec<(u16, u16, u16)>,
}

impl Link {
    pub fn contains(&self, at: (u16, u16)) -> bool {
        self.rows
            .iter()
            .any(|&(y, x, w)| at.1 == y && at.0 >= x && at.0 < x + w)
    }

    pub fn target(&self) -> &str {
        &self.target
    }

    pub fn kind(&self) -> LinkKind {
        self.kind
    }

    pub fn rows(&self) -> &[(u16, u16, u16)] {
        &self.rows
    }

    /// Hovering any row of a wrapped label highlights every row of it.
    pub fn paint_hover(&self, buffer: &mut Buffer) {
        for &(y, x, w) in &self.rows {
            for x in x..x + w {
                buffer[(x, y)].set_style(theme::link_hover_style());
            }
        }
    }
}

/// Place `linked`'s runs on screen in `rect`, scrolled `offset` rows; lines `Paragraph` wraps get none.
pub fn screen_links(linked: &LinkedLines, prewrapped: bool, rect: Rect, offset: u16) -> Vec<Link> {
    if linked.targets.is_empty() {
        return Vec::new();
    }
    let mut rows: Vec<Vec<(u16, u16, u16)>> = vec![Vec::new(); linked.targets.len()];
    let mut start = 0usize;
    for (line, runs) in linked.lines.iter().zip(&linked.runs) {
        let height = match prewrapped {
            true => 1,
            false => Paragraph::new(line.clone())
                .wrap(Wrap { trim: false })
                .line_count(rect.width),
        };
        let row = start.checked_sub(offset as usize);
        start += height;
        let Some(row) = row else {
            continue;
        };
        if row >= rect.height as usize {
            break;
        }
        if height > 1 {
            continue;
        }
        for run in runs {
            let Ok(x) = u16::try_from(run.x) else {
                continue;
            };
            let width = u16::try_from(run.width)
                .unwrap_or(u16::MAX)
                .min(rect.width.saturating_sub(x));
            if width > 0 {
                rows[run.link].push((rect.y + row as u16, rect.x + x, width));
            }
        }
    }
    rows.into_iter()
        .zip(&linked.targets)
        .filter(|(rows, _)| !rows.is_empty())
        .map(|(rows, target)| Link {
            target: target.url.clone(),
            kind: target.kind,
            rows,
        })
        .collect()
}
