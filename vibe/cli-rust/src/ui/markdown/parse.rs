//! Markdown source to styled blocks, driven by the pulldown-cmark event stream.

use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd};
use ratatui::style::{Modifier, Style};

use super::parse_table::TableB;
use super::text::collapse_ws;
use super::{fence, Block, Item, Profile, Sc};
use crate::ui::theme;

pub(super) struct Parsed {
    pub blocks: Vec<Block>,
    /// Link targets; an `Sc`'s link id indexes them.
    pub links: Vec<String>,
}

/// Drive the `pulldown-cmark` event stream into styled blocks, lists nesting by depth.
pub fn parse(text: &str) -> Vec<Block> {
    parse_with_links(text, Profile::Assistant).blocks
}

/// Drive the `pulldown-cmark` event stream into styled blocks and links.
pub(super) fn parse_with_links(text: &str, profile: Profile) -> Parsed {
    let base = Style::default().fg(theme::foreground());
    let mut b = Builder {
        blocks: Vec::new(),
        inline: Vec::new(),
        styles: vec![base],
        stack: Vec::new(),
        in_quote: false,
        link: None,
        link_saves: Vec::new(),
        links: Vec::new(),
        code: None,
        lang: String::new(),
        table: None,
        profile,
    };
    for event in Parser::new_ext(text, Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TABLES) {
        b.event(event);
    }
    Parsed {
        blocks: b.blocks,
        links: b.links,
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

struct Builder {
    blocks: Vec<Block>,
    inline: Vec<Sc>,
    styles: Vec<Style>,
    stack: Vec<Frame>,
    in_quote: bool,
    /// The open explicit link's index in `links`; autolinking must not run inside it.
    link: Option<usize>,
    /// Saved `link` values for an open link or image, restored at its end; Textual's later spans win.
    link_saves: Vec<Option<usize>>,
    links: Vec<String>,
    code: Option<String>,
    /// The fence's language, empty for indented blocks and bare fences.
    lang: String,
    table: Option<TableB>,
    profile: Profile,
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

    fn open_link(&mut self, target: String) -> usize {
        self.links.push(target);
        self.links.len() - 1
    }

    fn push_chars(&mut self, s: &str, style: Style, link: Option<usize>) {
        self.inline_mut()
            .extend(s.chars().map(|c| (c, style, link)));
    }

    fn push_str(&mut self, s: &str) {
        self.push_chars(s, self.style(), self.link);
    }

    /// Append body text, styling bare URLs and emails as links (markdown-it linkify).
    fn push_body(&mut self, s: &str) {
        if self.link.is_some() {
            self.push_str(s);
            return;
        }
        for (run, href) in super::autolink::split(s) {
            match href {
                None => self.push_str(run),
                Some(href) => {
                    let link = Some(self.open_link(href));
                    self.push_chars(run, link_style(self.style()), link);
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
                let style = match self.profile {
                    // Chat overrides `.code_inline` to $success + bold.
                    Profile::Assistant => self
                        .style()
                        .fg(theme::md_code_inline())
                        .add_modifier(Modifier::BOLD),
                    // The widget default: no bold, warning-washed fg over a bg.
                    Profile::Widget => {
                        let (fg, bg) = theme::md_widget_code_inline();
                        self.style().fg(fg).bg(bg)
                    }
                };
                self.push_chars(&t, style, self.link);
            }
            Event::SoftBreak => self.push_str(" "),
            Event::HardBreak => self.push_str("\n"),
            Event::Rule => {
                // Like fences, rules lift out of quotes and items.
                self.push_block(Block::Rule);
            }
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
            Tag::Strikethrough => self
                .styles
                .push(self.style().add_modifier(Modifier::CROSSED_OUT)),
            Tag::Link { dest_url, .. } => {
                self.link_saves.push(self.link);
                self.link = Some(self.open_link(dest_url.into_string()));
                self.styles.push(link_style(self.style()));
            }
            Tag::Image { dest_url, .. } => {
                self.link_saves.push(self.link);
                let link = self
                    .link
                    .unwrap_or_else(|| self.open_link(dest_url.into_string()));
                let style = link_style(self.style());
                self.push_chars("🖼  ", style, Some(link));
                self.link = Some(link);
                self.styles.push(style);
            }
            Tag::Table(_) => self.table = Some(TableB::default()),
            Tag::TableHead => {
                if let Some(t) = &mut self.table {
                    t.start_head();
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
            TagEnd::Emphasis | TagEnd::Strong | TagEnd::Strikethrough => {
                self.styles.pop();
            }
            TagEnd::Link | TagEnd::Image => {
                self.link = self.link_saves.pop().flatten();
                self.styles.pop();
            }
            TagEnd::TableCell => {
                let cell = std::mem::take(&mut self.inline);
                if let Some(t) = &mut self.table {
                    t.end_cell(cell);
                }
            }
            TagEnd::TableHead => {
                if let Some(t) = &mut self.table {
                    t.end_head();
                }
            }
            TagEnd::TableRow => {
                if let Some(t) = &mut self.table {
                    t.end_row();
                }
            }
            TagEnd::Table => {
                if let Some(t) = self.table.take() {
                    self.push_block(t.finish());
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
