//! Link hit testing for rendered markdown links.

use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd};
use ratatui::style::{Modifier, Style};
use ratatui::{buffer::Buffer, layout::Rect};

use crate::ui::theme;

#[derive(Clone, Copy)]
pub enum LinkKind {
    External,
    Attachment,
}

#[derive(Clone)]
pub struct Link {
    target: String,
    kind: LinkKind,
    /// Painted spans as `(y, x, width)`, one per screen row the label covers.
    rows: Vec<(u16, u16, u16)>,
}

impl Link {
    pub(crate) fn contains(&self, at: (u16, u16)) -> bool {
        self.rows
            .iter()
            .any(|&(y, x, w)| at.1 == y && at.0 >= x && at.0 < x + w)
    }

    pub(crate) fn target(&self) -> &str {
        &self.target
    }

    pub(crate) fn kind(&self) -> LinkKind {
        self.kind
    }

    /// Textual repaints only the row the pointer is on, not every row a wrapped
    /// label covers.
    pub(crate) fn paint_hover(&self, buffer: &mut Buffer, at: (u16, u16)) {
        for &(y, x, w) in self.rows.iter().filter(|&&(y, ..)| y == at.1) {
            for x in x..x + w {
                buffer[(x, y)].set_style(theme::link_hover_style());
            }
        }
    }
}

/// One horizontal stretch of link-styled cells.
struct Run {
    y: u16,
    x: u16,
    w: u16,
    text: String,
}

/// Pair the link-styled cells painted in `area` with the targets they came from.
pub(super) fn collect(
    buffer: &Buffer,
    area: Rect,
    pairs: &[(String, String)],
    kind: LinkKind,
) -> Vec<Link> {
    let runs = runs(buffer, area, kind);
    let mut links = Vec::new();
    let (mut r, mut t) = (0, 0);
    while r < runs.len() && t < pairs.len() {
        let head = runs[r].text.trim();
        let Some(start) = (t..pairs.len()).find(|&i| opens(&pairs[i].0, head)) else {
            // A label clipped by the viewport top leaves runs no target claims.
            r += 1;
            continue;
        };
        let label = normalize(&pairs[start].0);
        let mut at = 0;
        let mut rows = Vec::new();
        while r < runs.len() {
            let text = runs[r].text.trim();
            if text.is_empty() {
                break;
            }
            // Inline code inside a label keeps its own style, so runs may skip ahead.
            let Some(pos) = label[at..].find(text) else {
                break;
            };
            at += pos + text.len();
            rows.push((runs[r].y, runs[r].x, runs[r].w));
            r += 1;
            if at == label.len() {
                break;
            }
        }
        links.push(Link {
            target: pairs[start].1.clone(),
            kind,
            rows,
        });
        t = start + 1;
    }
    links
}

fn runs(buffer: &Buffer, area: Rect, kind: LinkKind) -> Vec<Run> {
    let mut out = Vec::new();
    for y in area.y..area.bottom() {
        let mut cur: Option<Run> = None;
        for x in area.x..area.right() {
            let cell = &buffer[(x, y)];
            if is_link(cell.style(), kind) {
                let run = cur.get_or_insert(Run {
                    y,
                    x,
                    w: 0,
                    text: String::new(),
                });
                run.w += 1;
                run.text.push_str(cell.symbol());
            } else if let Some(run) = cur.take() {
                out.push(run);
            }
        }
        out.extend(cur);
    }
    out
}

fn is_link(style: Style, kind: LinkKind) -> bool {
    let color = match kind {
        LinkKind::External => theme::md_link(),
        LinkKind::Attachment => theme::success(),
    };
    style.fg == Some(color) && style.add_modifier.contains(Modifier::UNDERLINED)
}

/// Whether `text` is how the rendered `label` starts.
fn opens(label: &str, text: &str) -> bool {
    !text.is_empty() && normalize(label).starts_with(text)
}

fn normalize(label: &str) -> String {
    label.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// The `(label, url)` pairs a markdown source declares, in document order.
pub fn targets(source: &str) -> Vec<(String, String)> {
    let mut targets = Targets::default();
    for event in Parser::new_ext(
        source,
        Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TABLES,
    ) {
        targets.event(&event);
    }
    targets.finish()
}

/// Collect link targets from the same event stream that builds Markdown blocks.
#[derive(Default)]
pub(super) struct Targets {
    values: Vec<(String, String)>,
    active: Option<(String, String)>,
}

impl Targets {
    pub fn event(&mut self, event: &Event<'_>) {
        match event {
            Event::Start(Tag::Link { dest_url, .. }) => {
                self.active = Some((String::new(), dest_url.to_string()));
            }
            Event::Code(text) => {
                if let Some((label, _)) = &mut self.active {
                    label.push_str(text);
                }
            }
            Event::Text(text) => match &mut self.active {
                Some((label, _)) => label.push_str(text),
                None => self.values.extend(
                    super::autolink::split(text)
                        .into_iter()
                        .filter_map(|(run, href)| Some((run.to_owned(), href?))),
                ),
            },
            Event::SoftBreak | Event::HardBreak => {
                if let Some((label, _)) = &mut self.active {
                    label.push(' ');
                }
            }
            Event::End(TagEnd::Link) => {
                if let Some((label, target)) =
                    self.active.take().filter(|(label, _)| !label.is_empty())
                {
                    self.values.push((label, target));
                }
            }
            _ => {}
        }
    }

    pub fn finish(self) -> Vec<(String, String)> {
        self.values
    }
}
