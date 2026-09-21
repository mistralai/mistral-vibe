//! Markdown source to styled blocks, driven by the pulldown-cmark event stream.

use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd};
use ratatui::style::{Modifier, Style};

use super::{fence, links, Block, Item, Sc};
use crate::ui::theme;

pub(super) struct Parsed {
    pub blocks: Vec<Block>,
    pub links: Vec<(String, String)>,
}

/// Drive the `pulldown-cmark` event stream into styled blocks, lists nesting by depth.
pub fn parse(text: &str) -> Vec<Block> {
    parse_with_links(text).blocks
}

/// Drive the `pulldown-cmark` event stream into styled blocks and links.
pub(super) fn parse_with_links(text: &str) -> Parsed {
    let base = Style::default().fg(theme::foreground());
    let mut b = Builder {
        blocks: Vec::new(),
        inline: Vec::new(),
        styles: vec![base],
        stack: Vec::new(),
        in_quote: false,
        in_link: false,
        code: None,
        lang: String::new(),
        table: None,
    };
    let mut links = links::Targets::default();
    for event in Parser::new_ext(text, Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TABLES) {
        links.event(&event);
        b.event(event);
    }
    Parsed {
        blocks: b.blocks,
        links: links.finish(),
    }
}

/// An open list or item on the nesting stack.
enum Frame {
    List {
        start: Option<u64>,
        items: Vec<Item>,
    },
    Item {
        inline: Vec<Sc>,
        children: Vec<Block>,
    },
}

/// A table being assembled: headers, rows, the row in progress, and head flag.
#[derive(Default)]
struct TableB {
    headers: Vec<Vec<Sc>>,
    rows: Vec<Vec<Vec<Sc>>>,
    row: Vec<Vec<Sc>>,
    in_head: bool,
}

struct Builder {
    blocks: Vec<Block>,
    inline: Vec<Sc>,
    styles: Vec<Style>,
    stack: Vec<Frame>,
    in_quote: bool,
    /// Inside an explicit markdown link, where autolinking must not run again.
    in_link: bool,
    code: Option<String>,
    /// The fence's language, empty for indented blocks and bare fences.
    lang: String,
    table: Option<TableB>,
}

impl Builder {
    fn style(&self) -> Style {
        *self.styles.last().unwrap()
    }

    /// The inline buffer to append text to: innermost open item, else the scratch.
    fn inline_mut(&mut self) -> &mut Vec<Sc> {
        match self.stack.last_mut() {
            Some(Frame::Item { inline, .. }) => inline,
            _ => &mut self.inline,
        }
    }

    /// Route a finished block to the innermost open item, else the top level.
    fn push_block(&mut self, block: Block) {
        match self.stack.last_mut() {
            Some(Frame::Item { children, .. }) => children.push(block),
            _ => self.blocks.push(block),
        }
    }

    fn push_str(&mut self, s: &str) {
        let style = self.style();
        self.inline_mut().extend(s.chars().map(|c| (c, style)));
    }

    /// Append body text, styling bare URLs and emails as links (markdown-it linkify).
    fn push_body(&mut self, s: &str) {
        if self.in_link {
            self.push_str(s);
            return;
        }
        for (run, href) in super::autolink::split(s) {
            match href {
                None => self.push_str(run),
                Some(_) => {
                    let style = link_style(self.style());
                    self.inline_mut().extend(run.chars().map(|c| (c, style)));
                }
            }
        }
    }

    fn event(&mut self, event: Event) {
        match event {
            Event::Start(tag) => self.start(tag),
            Event::End(tag) => self.end(tag),
            Event::Text(t) => {
                if let Some(code) = &mut self.code {
                    code.push_str(&t);
                } else {
                    self.push_body(&collapse_ws(&t));
                }
            }
            Event::Code(t) => {
                let style = self
                    .style()
                    .fg(theme::md_code_inline())
                    .add_modifier(Modifier::BOLD);
                self.inline_mut().extend(t.chars().map(|c| (c, style)));
            }
            Event::SoftBreak | Event::HardBreak => self.push_str(" "),
            _ => {}
        }
    }

    fn start(&mut self, tag: Tag) {
        match tag {
            Tag::Heading { level, .. } => self.styles.push(
                Style::default()
                    .fg(theme::md_heading(super::heading::level(level)))
                    .add_modifier(super::heading::modifier(level)),
            ),
            Tag::BlockQuote => self.in_quote = true,
            Tag::CodeBlock(kind) => {
                self.code = Some(String::new());
                self.lang = fence::lang(&kind);
            }
            Tag::List(start) => self.stack.push(Frame::List {
                start,
                items: Vec::new(),
            }),
            Tag::Item => self.stack.push(Frame::Item {
                inline: Vec::new(),
                children: Vec::new(),
            }),
            Tag::Emphasis => self
                .styles
                .push(self.style().add_modifier(Modifier::ITALIC)),
            Tag::Strong => self.styles.push(self.style().add_modifier(Modifier::BOLD)),
            Tag::Link { .. } => {
                self.in_link = true;
                self.styles.push(link_style(self.style()));
            }
            Tag::Table(_) => self.table = Some(TableB::default()),
            Tag::TableHead => {
                if let Some(t) = &mut self.table {
                    t.in_head = true;
                }
            }
            _ => {}
        }
    }

    fn end(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Heading(level) => {
                let inline = std::mem::take(&mut self.inline);
                self.blocks
                    .push(Block::Heading(super::heading::level(level), inline));
                self.styles.pop();
            }
            TagEnd::Paragraph => self.flush_paragraph(),
            TagEnd::Item => {
                if let Some(Frame::Item { inline, children }) = self.stack.pop() {
                    if let Some(Frame::List { items, .. }) = self.stack.last_mut() {
                        items.push(Item { inline, children });
                    }
                }
            }
            TagEnd::List(_) => {
                if let Some(Frame::List { start, items }) = self.stack.pop() {
                    self.push_block(Block::List { start, items });
                }
            }
            TagEnd::BlockQuote => {
                if !self.inline.is_empty() {
                    let inline = std::mem::take(&mut self.inline);
                    self.push_block(Block::Quote(inline));
                }
                self.in_quote = false;
            }
            TagEnd::CodeBlock => {
                if let Some(code) = self.code.take() {
                    let code = code.trim_end_matches('\n');
                    let lang = std::mem::take(&mut self.lang);
                    self.push_block(Block::Code(fence::lines(code, &lang)));
                }
            }
            TagEnd::Emphasis | TagEnd::Strong => {
                self.styles.pop();
            }
            TagEnd::Link => {
                self.in_link = false;
                self.styles.pop();
            }
            TagEnd::TableCell => {
                let cell = std::mem::take(&mut self.inline);
                if let Some(t) = &mut self.table {
                    if t.in_head {
                        t.headers.push(cell);
                    } else {
                        t.row.push(cell);
                    }
                }
            }
            TagEnd::TableHead => {
                if let Some(t) = &mut self.table {
                    t.in_head = false;
                }
            }
            TagEnd::TableRow => {
                if let Some(t) = &mut self.table {
                    t.rows.push(std::mem::take(&mut t.row));
                }
            }
            TagEnd::Table => {
                if let Some(t) = self.table.take() {
                    self.push_block(Block::Table {
                        headers: t.headers,
                        rows: t.rows,
                    });
                }
            }
            _ => {}
        }
    }

    /// End of a paragraph; only top-level paragraphs and blockquotes flush here.
    fn flush_paragraph(&mut self) {
        if self.code.is_some() {
            return;
        }
        let inline = std::mem::take(&mut self.inline);
        if inline.is_empty() {
            return;
        }
        if self.in_quote {
            self.push_block(Block::Quote(inline));
        } else {
            self.push_block(Block::Paragraph(inline));
        }
    }
}

fn link_style(style: Style) -> Style {
    style
        .fg(theme::md_link())
        .add_modifier(Modifier::UNDERLINED)
}

/// Textual collapses whitespace runs per text token, never across tokens.
fn collapse_ws(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if !c.is_whitespace() {
            out.push(c);
        } else if !out.ends_with(' ') {
            out.push(' ');
        }
    }
    out
}
