//! Assistant markdown, rendered to match Textual's `Markdown` widget cell-for-cell.

mod autolink;
mod cache;
mod fence;
mod heading;
mod links;
mod parse;
mod render;
mod table;
mod table_layout;
mod text;

use std::borrow::Cow;
use std::sync::Arc;

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::{buffer::Buffer, layout::Rect};

pub use autolink::split;
pub use cache::{MarkdownCache, MAX_MARKDOWN_CACHE_BYTES, MAX_MARKDOWN_CACHE_ENTRIES};
pub use fence::{lang as fence_lang, lines as fence_lines};
pub use links::targets;
pub(crate) use links::{Link, LinkKind};
pub use parse::parse;

const PAD: usize = 2;

/// How a markdown body is framed. The assistant widget pads its Markdown two
/// columns either side and centers top-level headings; the guttered startup
/// banners (Python `.whats-new-message` &c) pad none and left-align them.
#[derive(Clone, Copy)]
pub(crate) struct Frame {
    pad: usize,
    center_h1: bool,
}
pub(crate) const ASSISTANT: Frame = Frame {
    pad: PAD,
    center_h1: true,
};
pub(crate) const BANNER: Frame = Frame {
    pad: 0,
    center_h1: false,
};

/// A styled character: the atom the layout wraps and colors.
pub type Sc = (char, Style);

/// One logical markdown table cell and its wrapped rendered geometry.
#[derive(Clone)]
pub(crate) struct TableCell {
    pub table: usize,
    pub row: usize,
    pub column: usize,
    pub area: Rect,
    pub spans: Vec<TableCellSpan>,
    pub text: Arc<str>,
}

#[derive(Clone)]
pub(crate) struct TableCellSpan {
    pub y: u16,
    pub x0: u16,
    pub x1: u16,
    pub text_start: usize,
    pub text_end: usize,
}

pub struct PreparedMarkdown {
    lines: Vec<Line<'static>>,
    cells: Vec<TableCell>,
    links: Vec<(String, String)>,
    prewrapped: bool,
}

impl PreparedMarkdown {
    pub fn lines(&self) -> &[Line<'static>] {
        &self.lines
    }

    pub(crate) fn cells(&self) -> &[TableCell] {
        &self.cells
    }

    pub(crate) fn links(&self) -> &[(String, String)] {
        &self.links
    }

    pub(crate) fn prewrapped(&self) -> bool {
        self.prewrapped
    }

    fn retained_bytes(&self) -> usize {
        let lines = self.lines.capacity() * std::mem::size_of::<Line<'static>>()
            + self
                .lines
                .iter()
                .map(|line| {
                    line.spans.capacity() * std::mem::size_of::<Span<'static>>()
                        + line
                            .spans
                            .iter()
                            .map(|span| match &span.content {
                                Cow::Borrowed(_) => 0,
                                Cow::Owned(content) => content.capacity(),
                            })
                            .sum::<usize>()
                })
                .sum::<usize>();
        let cells = self.cells.capacity() * std::mem::size_of::<TableCell>()
            + self
                .cells
                .iter()
                .map(|cell| {
                    cell.text.len() + cell.spans.capacity() * std::mem::size_of::<TableCellSpan>()
                })
                .sum::<usize>();
        let links = self.links.capacity() * std::mem::size_of::<(String, String)>()
            + self
                .links
                .iter()
                .map(|(label, target)| label.capacity() + target.capacity())
                .sum::<usize>();
        std::mem::size_of::<Self>() + lines + cells + links
    }
}

pub enum Block {
    Heading(u8, Vec<Sc>),
    Paragraph(Vec<Sc>),
    /// Fence body, already syntax-highlighted: one span run per source line.
    Code(Vec<Vec<Span<'static>>>),
    List {
        start: Option<u64>,
        items: Vec<Item>,
    },
    Quote(Vec<Sc>),
    Table {
        headers: Vec<Vec<Sc>>,
        rows: Vec<Vec<Vec<Sc>>>,
    },
}

/// One list entry: its marker row text plus any blocks nested under it.
pub struct Item {
    pub inline: Vec<Sc>,
    pub children: Vec<Block>,
}

/// Render an assistant message body, including the block margins Textual inserts.
pub fn render(text: &str, width: u16) -> Vec<Line<'static>> {
    prepare_uncached(text, width).lines
}

/// A markdown body under a heavy left border (Python `.whats-new-message`,
/// `.vscode-extension-promo-message`, `.custom-tools-deprecation-message`): the
/// markdown is padded one column and the gutter bar paints the border on the
/// left of every row. First/last block margins are zeroed by their tcss, so the
/// leading and trailing blank rows the plain renderer inserts are dropped.
pub fn guttered(text: &str, width: u16, color: ratatui::style::Color) -> Vec<Line<'static>> {
    let mut body = prepare(text, width.saturating_sub(2), BANNER).lines;
    while body
        .first()
        .is_some_and(|line| line.spans.iter().all(|span| span.content.trim().is_empty()))
    {
        body.remove(0);
    }
    if body.len() > 1
        && body[1]
            .spans
            .iter()
            .all(|span| span.content.trim().is_empty())
    {
        body.remove(1);
    }
    while body
        .last()
        .is_some_and(|line| line.spans.iter().all(|span| span.content.trim().is_empty()))
    {
        body.pop();
    }
    let gutter = Style::default().fg(color);
    body.into_iter()
        .map(|mut line| {
            line.spans.insert(0, Span::styled("┃ ", gutter));
            line
        })
        .collect()
}

/// Render untrusted code as literal highlighted lines without Markdown parsing.
pub(crate) fn code_lines(code: &str, lang: &str) -> Vec<Vec<Span<'static>>> {
    fence::lines(code, lang)
}

pub(crate) fn prepare_uncached(text: &str, width: u16) -> PreparedMarkdown {
    prepare(text, width, ASSISTANT)
}

/// Render output sinks: the lines, the table-cell metadata, and the table counter.
pub(crate) struct Out {
    pub(super) lines: Vec<Line<'static>>,
    pub(super) cells: Vec<TableCell>,
    pub(super) table: usize,
}

fn prepare(text: &str, width: u16, frame: Frame) -> PreparedMarkdown {
    let parsed = parse::parse_with_links(text);
    let mut out = Out {
        lines: Vec::new(),
        cells: Vec::new(),
        table: 0,
    };
    let mut prev_bottom: Option<usize> = None;
    for block in &parsed.blocks {
        let (top, bottom) = margins(block);
        let gap = match prev_bottom {
            None => 1 + top,
            Some(previous) => previous.max(top),
        };
        for _ in 0..gap {
            out.lines.push(Line::from(""));
        }
        render::render_block(block, width, frame, &mut out);
        prev_bottom = Some(bottom);
    }
    let prewrapped = width > 0 && out.lines.iter().all(|line| line.width() <= width as usize);
    PreparedMarkdown {
        lines: out.lines,
        cells: out.cells,
        links: parsed.links,
        prewrapped,
    }
}

pub(super) fn links(
    buffer: &Buffer,
    area: Rect,
    pairs: &[(String, String)],
    kind: LinkKind,
) -> Vec<Link> {
    links::collect(buffer, area, pairs, kind)
}

/// (top, bottom) margin in blank rows from each block's Textual `margin`.
fn margins(b: &Block) -> (usize, usize) {
    match b {
        Block::Heading(1 | 2, _) => (2, 1),
        Block::Heading(_, _) => (1, 1),
        // `MarkdownFence:ansi` sets `margin: 0`; truecolor themes keep `margin: 1 0`.
        Block::Code(_) if super::theme::is_ansi() => (0, 0),
        Block::Code(_) | Block::Quote(_) => (1, 1),
        _ => (0, 1),
    }
}
