//! Assistant markdown, rendered to match Textual's `Markdown` widget cell-for-cell.

mod autolink;
mod cache;
mod fence;
mod heading;
mod links;
mod parse;
mod parse_table;
mod render;
mod table;
mod table_layout;
mod text;

use std::borrow::Cow;
use std::sync::Arc;

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};

pub use autolink::split;
pub use cache::{MarkdownCache, MAX_MARKDOWN_CACHE_BYTES, MAX_MARKDOWN_CACHE_ENTRIES};
pub use fence::{lang as fence_lang, lines as fence_lines};
pub use links::{screen_links, Link, LinkKind, LinkedLines};
pub use parse::parse;
pub(crate) use text::{cell_width, wrap_chars};

const PAD: usize = 2;

/// How a markdown body is framed. The assistant widget pads its Markdown two
/// columns either side and centers top-level headings; the guttered startup
/// banners (Python `.whats-new-message` &c) pad none and left-align them.
#[derive(Clone, Copy)]
pub(crate) struct Frame {
    pad: usize,
    center_h1: bool,
    profile: Profile,
}
pub(crate) const ASSISTANT: Frame = Frame {
    pad: PAD,
    center_h1: true,
    profile: Profile::Assistant,
};
pub(crate) const BANNER: Frame = Frame {
    pad: 0,
    center_h1: false,
    profile: Profile::Assistant,
};
/// A standalone Textual `Markdown` widget (onboarding preview): the widget's
/// default styles, without vibe's `app.tcss` overrides.
pub(crate) const WIDGET: Frame = Frame {
    pad: PAD,
    center_h1: true,
    profile: Profile::Widget,
};

/// The style set a body renders with.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Profile {
    /// Chat assistant messages — vibe's `Markdown` overrides in `app.tcss`.
    Assistant,
    /// A plain `Markdown` widget — Textual's own defaults.
    Widget,
}

/// A styled character and the link it belongs to: the atom the layout wraps and colors.
pub type Sc = (char, Style, Option<usize>);

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
    lines: LinkedLines,
    cells: Vec<TableCell>,
    prewrapped: bool,
}

impl PreparedMarkdown {
    fn from_lines(lines: LinkedLines) -> Self {
        Self {
            lines,
            cells: Vec::new(),
            prewrapped: false,
        }
    }

    pub fn lines(&self) -> &[Line<'static>] {
        self.lines.lines()
    }

    pub fn linked(&self) -> &LinkedLines {
        &self.lines
    }

    pub(crate) fn cells(&self) -> &[TableCell] {
        &self.cells
    }

    pub(crate) fn prewrapped(&self) -> bool {
        self.prewrapped
    }

    fn retained_bytes(&self) -> usize {
        let lines = self
            .lines
            .lines()
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
        std::mem::size_of::<Self>() + lines + cells + self.lines.retained_bytes()
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
    /// Thematic break `---` — a full-width horizontal rule.
    Rule,
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
    prepare_uncached(text, width).lines.into_lines()
}

/// Render a standalone `Markdown` widget body (Textual's default styles).
pub fn render_widget(text: &str, width: u16) -> Vec<Line<'static>> {
    prepare(text, width, WIDGET, false).lines.into_lines()
}

/// Render a command-result body (Python `.user-command-content`): its tcss
/// strips the first block's margins and every heading's, so the first block
/// and every heading follow their content directly and a gap is only the
/// previous block's bottom margin.
pub fn command_result(text: &str, width: u16) -> Vec<Line<'static>> {
    prepare(text, width, ASSISTANT, true).lines.into_lines()
}

/// A markdown body under a heavy left border (Python `.whats-new-message`,
/// `.vscode-extension-promo-message`, `.custom-tools-deprecation-message`): the
/// markdown is padded one column and the gutter bar paints the border on the
/// left of every row. First/last block margins are zeroed by their tcss, so the
/// leading and trailing blank rows the plain renderer inserts are dropped.
pub fn guttered(text: &str, width: u16, color: ratatui::style::Color) -> LinkedLines {
    let mut body = prepare(text, width.saturating_sub(2), BANNER, false).lines;
    let blank = |line: &Line<'static>| line.spans.iter().all(|span| span.content.trim().is_empty());
    let first = body
        .lines()
        .iter()
        .position(|line| !blank(line))
        .unwrap_or(body.len());
    let end = body
        .lines()
        .iter()
        .rposition(|line| !blank(line))
        .map_or(first, |last| last + 1);
    let gap = body
        .lines()
        .get(first + 1)
        .is_some_and(blank)
        .then_some(first + 1);
    body.retain(|index, _| index >= first && index < end && Some(index) != gap);
    let gutter = Style::default().fg(color);
    body.prefix(|_| Span::styled("┃ ", gutter));
    body
}

/// Render untrusted code as literal highlighted lines without Markdown parsing.
pub(crate) fn code_lines(code: &str, lang: &str) -> Vec<Vec<Span<'static>>> {
    fence::lines(code, lang)
}

pub(crate) fn prepare_uncached(text: &str, width: u16) -> PreparedMarkdown {
    prepare(text, width, ASSISTANT, false)
}

/// Render output sinks: the lines and their links, the table-cell metadata, and the table counter.
pub(crate) struct Out {
    pub(super) lines: LinkedLines,
    pub(super) cells: Vec<TableCell>,
    pub(super) table: usize,
}

fn prepare(text: &str, width: u16, frame: Frame, command: bool) -> PreparedMarkdown {
    let parsed = parse::parse_with_links(text, frame.profile);
    let mut out = Out {
        lines: LinkedLines::default(),
        cells: Vec::new(),
        table: 0,
    };
    for target in parsed.links {
        out.lines.link(target, LinkKind::External);
    }
    let mut prev_bottom: Option<usize> = None;
    for block in &parsed.blocks {
        // Command results (Python `.user-command-content`): a tcss rule that
        // touches one margin edge replaces the whole margin, so the
        // first-child rule strips the first block's margins entirely and the
        // MarkdownHeader rule strips every heading's.
        let (top, bottom) = match (command, prev_bottom.is_none()) {
            (true, true) => (0, 0),
            _ => margins(block, frame, command),
        };
        let gap = match prev_bottom {
            // A chat message opens with one blank row of its own; a bare widget
            // starts directly at the first block's top margin.
            None => match frame.profile {
                Profile::Widget => top,
                Profile::Assistant => 1 + top,
            },
            Some(previous) => previous.max(top),
        };
        for _ in 0..gap {
            out.lines.push(Line::from(""));
        }
        render::render_block(block, width, frame, &mut out);
        prev_bottom = Some(bottom);
    }
    if frame.profile == Profile::Widget {
        // The last block's bottom margin is part of the widget's scroll height.
        for _ in 0..prev_bottom.unwrap_or(0) {
            out.lines.push(Line::from(""));
        }
    }
    let prewrapped = width > 0
        && out
            .lines
            .lines()
            .iter()
            .all(|line| line.width() <= width as usize);
    PreparedMarkdown {
        lines: out.lines,
        cells: out.cells,
        prewrapped,
    }
}

/// (top, bottom) margin in blank rows from each block's Textual `margin`.
/// ``command`` strips heading margins entirely, mirroring the
/// `.user-command-content` tcss: a Textual rule that touches one margin edge
/// replaces the whole margin, so its `MarkdownHeader` rule zeroes top and
/// bottom together and every command-message heading sits directly against
/// its content.
fn margins(b: &Block, frame: Frame, command: bool) -> (usize, usize) {
    match b {
        Block::Heading(..) if command => (0, 0),
        Block::Heading(1 | 2, _) => (2, 1),
        Block::Heading(_, _) => (1, 1),
        // `MarkdownFence:ansi` sets `margin: 0`; truecolor themes keep `margin: 1 0`.
        Block::Code(_) if super::theme::is_ansi() => (0, 0),
        Block::Code(_) | Block::Quote(_) => (1, 1),
        // `MarkdownHorizontalRule`: `padding-top: 1; margin-bottom: 1`. The
        // assistant frame leaves the padding to this margin row; a bare
        // widget renders it inside the block, so its top margin is 0.
        Block::Rule => match frame.profile {
            Profile::Widget => (0, 1),
            Profile::Assistant => (1, 1),
        },
        _ => (0, 1),
    }
}
