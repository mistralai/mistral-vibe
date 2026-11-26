//! Keep sent long pastes collapsed: markers in the prompt's display content.

use std::collections::VecDeque;
use std::ops::Range;
use std::sync::Arc;

use serde_json::{json, Value};

/// Bound on remembered collapsed pastes; an older one is sent without a marker.
pub const MAX_COLLAPSED_PASTES: usize = 64;
/// The queued entry annotation the app server stores as the message's display content.
pub const DISPLAY_ANNOTATION: &str = "vibe.userDisplayContent";
const MARKER: &str = "vibe.collapsed_paste";

/// Texts collapsed in the composer, remembered so the prompts sending them
/// can mark where each one sits.
#[derive(Clone, Default)]
pub struct CollapsedPastes {
    texts: VecDeque<Arc<str>>,
}

impl CollapsedPastes {
    pub fn remember(&mut self, text: Arc<str>) {
        self.texts.retain(|known| *known != text);
        self.texts.push_back(text);
        if self.texts.len() > MAX_COLLAPSED_PASTES {
            self.texts.pop_front();
        }
    }

    /// Stop collapsing `text`, once the user asked to see it in full.
    pub fn forget(&mut self, text: &str) {
        self.texts.retain(|known| &**known != text);
    }

    /// Display content marking every occurrence of each remembered paste
    /// `text` holds, if any.
    pub fn display(&self, text: &str) -> Option<Value> {
        let mut found: Vec<(usize, &str, &str)> = self
            .texts
            .iter()
            .flat_map(|paste| {
                occurrences(text, paste).map(move |(start, sent)| (start, sent, &**paste))
            })
            .collect();
        found.sort_by_key(|&(start, sent, _)| (start, std::cmp::Reverse(sent.len())));
        let mut end = 0;
        let markers: Vec<Value> = found
            .into_iter()
            .filter(|&(start, sent, _)| {
                let kept = start >= end;
                if kept {
                    end = start + sent.len();
                }
                kept
            })
            .map(|(start, sent, paste)| {
                // `start` is a UTF-8 byte offset; `chars` and `pasted` count chars.
                json!({
                    "type": MARKER,
                    "start": start,
                    "chars": sent.chars().count(),
                    "hash": fingerprint(sent),
                    "pasted": paste.chars().count(),
                })
            })
            .collect();
        (!markers.is_empty()).then(|| json!({"version": "1", "host": "vibe", "content": markers}))
    }
}

/// Every place `paste` sits in the sent `text`: whole, or without the
/// surrounding whitespace the submit trim takes off when it opens or closes the
/// message. Overlapping finds are left for `display` to settle, longest first.
fn occurrences<'a>(text: &'a str, paste: &'a str) -> impl Iterator<Item = (usize, &'a str)> {
    [paste, paste.trim_end(), paste.trim_start(), paste.trim()]
        .into_iter()
        .filter(|candidate| !candidate.is_empty())
        .flat_map(move |candidate| {
            text.match_indices(candidate)
                .map(move |(start, _)| (start, candidate))
        })
}

/// The placeholder standing for a paste of `chars` characters.
pub fn label(chars: usize) -> String {
    format!("[Pasted {chars} characters]")
}

/// A sent message's text with its marked pastes collapsed, and where each
/// placeholder sits so a renderer can color it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Collapsed {
    pub text: String,
    /// Byte ranges of the placeholders in `text`, in order.
    pub placeholders: Vec<Range<usize>>,
}

impl From<String> for Collapsed {
    fn from(text: String) -> Self {
        Self {
            text,
            placeholders: Vec::new(),
        }
    }
}

impl From<&str> for Collapsed {
    fn from(text: &str) -> Self {
        text.to_owned().into()
    }
}

impl Collapsed {
    /// `prefix` then the first `max_chars` characters, with the placeholders
    /// shifted after the prefix and clipped to what is kept.
    pub fn truncated(&self, prefix: &str, max_chars: usize) -> Self {
        let kept = self
            .text
            .char_indices()
            .nth(max_chars)
            .map_or(self.text.len(), |(at, _)| at);
        let placeholders = self
            .placeholders
            .iter()
            .filter(|range| range.start < kept)
            .map(|range| prefix.len() + range.start..prefix.len() + range.end.min(kept))
            .collect();
        Self {
            text: format!("{prefix}{}", &self.text[..kept]),
            placeholders,
        }
    }

    /// Each line of the text, cut into `(segment, is_placeholder)` runs.
    pub fn lines(&self) -> impl Iterator<Item = Vec<(&str, bool)>> {
        self.text.lines().map(|line| {
            let line_start = line.as_ptr() as usize - self.text.as_ptr() as usize;
            segments(line, line_start, &self.placeholders)
        })
    }
}

/// `line`, starting at byte `line_start` of the text, cut into plain and
/// placeholder segments; a placeholder never spans lines.
fn segments<'t>(
    line: &'t str,
    line_start: usize,
    placeholders: &[Range<usize>],
) -> Vec<(&'t str, bool)> {
    let line_end = line_start + line.len();
    let mut out = Vec::new();
    let mut at = 0;
    for range in placeholders
        .iter()
        .filter(|range| range.start >= line_start && range.end <= line_end)
    {
        let (start, end) = (range.start - line_start, range.end - line_start);
        if start > at {
            out.push((&line[at..start], false));
        }
        out.push((&line[start..end], true));
        at = end;
    }
    if at < line.len() || out.is_empty() {
        out.push((&line[at..], false));
    }
    out
}

/// `text` with each marked paste shown as its `[Pasted n characters]` placeholder.
pub fn collapse(text: &str, display: Option<&Value>) -> String {
    collapse_marked(text, display).text
}

/// `collapse`, keeping where each placeholder lands in the collapsed text.
pub fn collapse_marked(text: &str, display: Option<&Value>) -> Collapsed {
    let mut out = String::with_capacity(text.len());
    let mut placeholders = Vec::new();
    let mut at = 0;
    for (start, end, chars) in ranges(text, display) {
        out.push_str(&text[at..start]);
        let from = out.len();
        out.push_str(&label(chars));
        placeholders.push(from..out.len());
        at = end;
    }
    out.push_str(&text[at..]);
    Collapsed {
        text: out,
        placeholders,
    }
}

/// The byte ranges of `text` its display content marks as collapsed pastes,
/// in order and without overlaps, each with its placeholder's character count.
pub fn ranges(text: &str, display: Option<&Value>) -> Vec<(usize, usize, usize)> {
    let mut ranges: Vec<(usize, usize, usize)> = display
        .and_then(|display| display.get("content"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some(MARKER))
        .filter_map(|block| marked_range(text, block))
        .collect();
    ranges.sort_unstable();
    let mut end = 0;
    ranges.retain(|&(start, stop, _)| {
        let kept = start >= end;
        if kept {
            end = stop;
        }
        kept
    });
    ranges
}

/// The byte range a marker names, when `text` still holds that exact paste
/// there, and the character count its placeholder showed in the composer.
fn marked_range(text: &str, marker: &Value) -> Option<(usize, usize, usize)> {
    let start = usize::try_from(marker.get("start")?.as_u64()?).ok()?;
    let chars = usize::try_from(marker.get("chars")?.as_u64()?).ok()?;
    let pasted = marker
        .get("pasted")
        .and_then(Value::as_u64)
        .and_then(|pasted| usize::try_from(pasted).ok())
        .unwrap_or(chars);
    let hash = marker.get("hash")?.as_str()?;
    let rest = text.get(start..)?;
    let len = match rest.char_indices().nth(chars) {
        Some((len, _)) => len,
        None if rest.chars().count() == chars => rest.len(),
        None => return None,
    };
    (fingerprint(&rest[..len]) == hash).then_some((start, start + len, pasted))
}

/// FNV-1a 64 of `text`, enough to tell a moved or edited paste from the marked one.
fn fingerprint(text: &str) -> String {
    let hash = text.bytes().fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
        (hash ^ u64::from(byte)).wrapping_mul(0x0000_0100_0000_01b3)
    });
    format!("{hash:016x}")
}
