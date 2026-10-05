//! Accepted composer mentions (`/skill`, `@file`, images, long pastes): colored and atomic.

use std::sync::Arc;

use crate::app::ChatInput;
use crate::utils::text_diff::changed_range_from;

/// Bound on tracked mentions; later accepted completions stay ordinary text.
pub const MAX_MENTIONS: usize = 256;

/// One mention's byte span, plus the pasted text a collapsed paste stands for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mention {
    pub start: usize,
    pub end: usize,
    pub paste: Option<Arc<str>>,
}

/// Accepted mentions, valid for the composer text they were last reconciled
/// against. An edit elsewhere shifts them; an edit touching one, or text glued
/// to either side of it, turns that mention back into plain text.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Mentions {
    spans: Vec<Mention>,
    text: String,
}

impl Mentions {
    pub fn clear(&mut self) {
        self.spans.clear();
        self.text.clear();
    }

    /// Mentions for `input`, or none while they still await a `sync`.
    pub fn spans(&self, input: &str) -> &[Mention] {
        match self.text == input {
            true => &self.spans,
            false => &[],
        }
    }

    /// Reconcile the spans with an edited `input`.
    pub fn sync(&mut self, input: &str) {
        self.sync_at(input, usize::MAX);
    }

    /// Reconcile the spans with `input`, edited from byte `at` onwards. The
    /// edit position keeps an insertion that repeats the text after it (a `[`
    /// typed before a `[Pasted …]` placeholder) from being read as an edit
    /// inside the mention.
    pub fn sync_at(&mut self, input: &str, at: usize) {
        if self.text == input {
            return;
        }
        let (prefix, old_end, new_end) = changed_range_from(&self.text, input, at);
        self.spans.retain_mut(|mention| {
            if mention.end <= prefix {
                return true;
            }
            if mention.start < old_end {
                return false;
            }
            mention.start = mention.start - old_end + new_end;
            mention.end = mention.end - old_end + new_end;
            true
        });
        self.spans
            .retain(|mention| delimited(input, mention.start, mention.end));
        self.text = input.to_owned();
    }

    /// Track `start..end` of `input` as an accepted mention, standing for
    /// `paste` when it collapses a long paste.
    pub fn add(&mut self, input: &str, start: usize, end: usize, paste: Option<Arc<str>>) {
        self.sync(input);
        if start >= end || !delimited(input, start, end) {
            return;
        }
        self.spans
            .retain(|mention| mention.end <= start || mention.start >= end);
        if self.spans.len() >= MAX_MENTIONS {
            return;
        }
        let at = self.spans.partition_point(|mention| mention.start < start);
        self.spans.insert(at, Mention { start, end, paste });
    }

    /// Replace the spans, as captured for `input` by an edit checkpoint.
    pub fn restore(&mut self, input: &str, spans: Vec<Mention>) {
        self.spans = spans;
        self.text = input.to_owned();
        self.spans
            .retain(|mention| delimited(input, mention.start, mention.end));
    }

    /// The mention strictly containing `offset`, where a caret may not rest.
    pub fn around(&self, offset: usize) -> Option<(usize, usize)> {
        self.spans
            .iter()
            .find(|mention| mention.start < offset && offset < mention.end)
            .map(|mention| (mention.start, mention.end))
    }

    /// Widen `lo..hi` to cover every mention it intersects.
    pub fn widen(&self, lo: usize, hi: usize) -> (usize, usize) {
        if lo == hi {
            return (lo, hi);
        }
        self.spans
            .iter()
            .filter(|mention| mention.start < hi && mention.end > lo)
            .fold((lo, hi), |(lo, hi), mention| {
                (lo.min(mention.start), hi.max(mention.end))
            })
    }

    /// The span of the placeholder collapsing exactly `paste`, if it is still there.
    pub fn span_of(&self, paste: &Arc<str>) -> Option<(usize, usize)> {
        self.spans
            .iter()
            .find(|mention| {
                mention
                    .paste
                    .as_ref()
                    .is_some_and(|own| Arc::ptr_eq(own, paste))
            })
            .map(|mention| (mention.start, mention.end))
    }

    /// Whether some placeholder collapses a paste of exactly `text`.
    pub fn holds_paste(&self, text: &str) -> bool {
        self.spans
            .iter()
            .any(|mention| mention.paste.as_deref() == Some(text))
    }

    /// `input` with every collapsed paste replaced by the text it stands for.
    pub fn expanded(&self, input: &str) -> String {
        expand_range(input, self.spans(input), 0, input.len())
    }

    /// `input[lo..hi]` with the collapsed pastes it holds whole shown in full.
    pub fn expanded_range(&self, input: &str, lo: usize, hi: usize) -> String {
        expand_range(input, self.spans(input), lo, hi)
    }

    /// Whether the spans were last reconciled against `input`.
    pub fn synced(&self, input: &str) -> bool {
        self.text == input
    }
}

impl ChatInput {
    pub fn sync_mentions(&mut self) {
        self.mentions.sync(&self.input);
    }

    /// Reconcile the mentions right after an edit starting at byte `at`.
    pub fn sync_mentions_at(&mut self, at: usize) {
        self.mentions.sync_at(&self.input, at);
    }

    /// Track `start..end` of the input as an accepted mention.
    pub fn add_mention(&mut self, start: usize, end: usize) {
        self.mentions.add(&self.input, start, end, None);
    }

    /// The input with its mode prefix and every collapsed paste expanded, as submitted.
    pub fn submitted_text(&mut self) -> String {
        self.sync_mentions();
        let body = self.mentions.expanded(&self.input);
        match self.mode.prefix() {
            Some(prefix) => format!("{prefix}{body}"),
            None => body,
        }
    }

    /// Run a text edit, widening whatever it removes to whole mentions, and
    /// return the removed text with its collapsed pastes in full. The edit
    /// starts at `starts_at` when given (a line deletion starts at the line
    /// start), else at the earlier caret: no other action edits before both.
    pub fn edit_atomically(
        &mut self,
        starts_at: Option<usize>,
        edit: impl FnOnce(&mut String, &mut usize, &mut Option<usize>),
    ) -> String {
        self.normalize_positions();
        self.sync_mentions();
        let before = self.input.clone();
        let spans = self.mentions.spans(&before).to_vec();
        let from = self
            .anchor
            .map_or(self.cursor, |anchor| anchor.min(self.cursor));
        edit(&mut self.input, &mut self.cursor, &mut self.anchor);
        let at = starts_at.unwrap_or(usize::MAX).min(from).min(self.cursor);
        let (prefix, old_end, new_end) = changed_range_from(&before, &self.input, at);
        let (lo, hi) = self.mentions.widen(prefix, old_end);
        if (lo, hi) != (prefix, old_end) {
            let inserted = self.input[prefix..new_end].to_owned();
            self.input = format!("{}{inserted}{}", &before[..lo], &before[hi..]);
            self.cursor = lo + inserted.len();
            self.anchor = None;
        }
        self.sync_mentions_at(lo.min(at));
        expand_range(&before, &spans, lo, hi)
    }

    /// Where a cut starts when it takes the caret line, as it does without a
    /// selection: that line's start.
    pub fn line_cut_start(&self) -> Option<usize> {
        crate::chat_input::selection_range(&self.input, self.cursor, self.anchor)
            .is_none()
            .then(|| crate::utils::input_edit::line_bounds(&self.input, self.cursor).0)
    }

    /// Replace the selection before an insertion, removing whole mentions.
    pub fn widen_selection(&mut self) {
        self.sync_mentions();
        let Some((lo, hi)) =
            crate::chat_input::selection_range(&self.input, self.cursor, self.anchor)
        else {
            return;
        };
        let (lo, hi) = self.mentions.widen(lo, hi);
        match self.anchor.is_some_and(|anchor| anchor <= self.cursor) {
            true => (self.anchor, self.cursor) = (Some(lo), hi),
            false => (self.anchor, self.cursor) = (Some(hi), lo),
        }
    }

    /// Keep the caret out of a mention: a move lands past it in the direction
    /// the caret travelled from `from`, and a selection covers it whole.
    pub fn snap_cursor(&mut self, from: usize) {
        self.sync_mentions();
        if self.anchor.is_some() {
            self.widen_selection();
            return;
        }
        if let Some((start, end)) = self.mentions.around(self.cursor) {
            self.cursor = if self.cursor < from { start } else { end };
        }
    }
}

/// A mention's span is intact. A `/skill` or `@path` one, which the app server
/// reads back from the text, must also stand alone, with whitespace or the text
/// edge on both sides; a placeholder may touch its neighbours.
fn delimited(input: &str, start: usize, end: usize) -> bool {
    if start >= end || end > input.len() {
        return false;
    }
    if !input.is_char_boundary(start) || !input.is_char_boundary(end) {
        return false;
    }
    if !input[start..].starts_with(['/', '@']) {
        return true;
    }
    input[..start]
        .chars()
        .next_back()
        .is_none_or(char::is_whitespace)
        && input[end..].chars().next().is_none_or(char::is_whitespace)
}

/// `input[lo..hi]` with each collapsed paste of `spans` inside it in full.
fn expand_range(input: &str, spans: &[Mention], lo: usize, hi: usize) -> String {
    let mut out = String::with_capacity(hi - lo);
    let mut at = lo;
    for mention in spans
        .iter()
        .filter(|mention| lo <= mention.start && mention.end <= hi)
    {
        let Some(paste) = &mention.paste else {
            continue;
        };
        out.push_str(&input[at..mention.start]);
        out.push_str(paste);
        at = mention.end;
    }
    out.push_str(&input[at..hi]);
    out
}
