//! Trust rows retain source ranges independently of wrapping and centering.

use std::ops::Range;
use std::sync::Arc;

use ratatui::style::Style;
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use crate::utils::text::wrap_hard;

#[derive(Clone)]
pub(super) struct Source {
    pub text: Arc<str>,
    pub range: Range<usize>,
    pub column: usize,
}

impl Source {
    pub fn selected(&self, left: usize, right: usize) -> Option<Range<usize>> {
        let mut column = self.column;
        let mut selected: Option<Range<usize>> = None;
        for (offset, grapheme) in self.text[self.range.clone()].grapheme_indices(true) {
            let end = column + grapheme.width();
            if column <= right && end > left {
                let start = self.range.start + offset;
                let range = selected.get_or_insert(start..start);
                range.end = start + grapheme.len();
            }
            column = end;
        }
        selected
    }
}

#[derive(Clone, Default)]
pub(super) struct Row {
    pub spans: Vec<(String, Style)>,
    pub sources: Vec<Source>,
}

impl Row {
    pub fn text(spans: Vec<(String, Style)>) -> Self {
        let text: Arc<str> = spans
            .iter()
            .map(|(text, _)| text.as_str())
            .collect::<String>()
            .into();
        let source = Source {
            range: 0..text.len(),
            text,
            column: 0,
        };
        Self {
            spans,
            sources: vec![source],
        }
    }

    pub fn decoration(spans: Vec<(String, Style)>) -> Self {
        Self {
            spans,
            sources: Vec::new(),
        }
    }

    pub fn widgets(spans: Vec<(String, Style)>) -> Self {
        let mut column = 0;
        let mut sources = Vec::new();
        for (text, _) in &spans {
            if !text.trim().is_empty() {
                sources.push(Source {
                    text: text.as_str().into(),
                    range: 0..text.len(),
                    column,
                });
            }
            column += text.width();
        }
        Self { spans, sources }
    }

    pub fn left(&self, left: u16, width: usize) -> u16 {
        let row_width: usize = self.spans.iter().map(|(text, _)| text.width()).sum();
        left + (width.saturating_sub(row_width) / 2) as u16
    }
}

pub(super) fn block(text: &str, width: usize, style: Style, margin: bool) -> Vec<Row> {
    let source: Arc<str> = text.into();
    let mut offset = 0;
    let mut rows = Vec::new();
    for line in text.lines().flat_map(|line| wrap_hard(line, width)) {
        let start = offset
            + source[offset..]
                .find(&line)
                .expect("wrapped row is source text");
        offset = start + line.len();
        rows.push(Row {
            spans: vec![(line, style)],
            sources: vec![Source {
                text: source.clone(),
                range: start..offset,
                column: 0,
            }],
        });
    }
    if margin {
        rows.push(Row::default());
    }
    rows
}
