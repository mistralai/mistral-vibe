//! Syntax highlighting: syntect grammars painted with the theme's token palette.

use crate::ui::theme::syntax as palette;

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use ratatui::text::Span;
use syntect::parsing::{ParseState, ScopeStack, SyntaxReference, SyntaxSet};
use syntect::util::LinesWithEndings;

use crate::ui::theme;

/// Above these, highlighting is skipped and the block renders as plain text.
const MAX_BYTES: usize = 512 * 1024;
const MAX_LINES: usize = 10_000;
const MAX_LINE_BYTES: usize = 4 * 1024;
/// Must exceed the distinct lines of rebuilt bodies, or the clear-on-full cache always misses.
const CACHE_CAP: usize = 4096;

/// One styled span run per source line.
pub type Highlighted = Vec<Vec<Span<'static>>>;

fn syntaxes() -> &'static SyntaxSet {
    static SET: OnceLock<SyntaxSet> = OnceLock::new();
    SET.get_or_init(two_face::syntax::extra_newlines)
}

/// Aliases the two-face syntax set cannot resolve on its own.
fn patch_alias(lang: &str) -> &str {
    match lang {
        "csharp" | "c-sharp" => "c#",
        "cu" | "cuh" | "cppm" | "cxxm" | "ixx" => "cpp",
        "golang" => "go",
        "python3" => "python",
        "shell" => "bash",
        other => other,
    }
}

fn find_syntax(lang: &str) -> Option<&'static SyntaxReference> {
    let set = syntaxes();
    let lower = lang.to_ascii_lowercase();
    let patched = patch_alias(&lower);
    set.find_syntax_by_token(patched)
        .or_else(|| set.find_syntax_by_name(patched))
        .or_else(|| {
            set.syntaxes()
                .iter()
                .find(|s| s.name.eq_ignore_ascii_case(patched))
        })
        .or_else(|| set.find_syntax_by_extension(lang))
}

fn too_large(code: &str) -> bool {
    code.len() > MAX_BYTES
        || code.lines().count() > MAX_LINES
        || code.lines().any(|l| l.len() > MAX_LINE_BYTES)
}

/// Highlight a whole block at once so parser state carries across lines
/// (docstrings, multi-line strings, block comments). `None` means "render plain".
pub fn code(code: &str, lang: &str) -> Option<Highlighted> {
    if plain(code, lang) {
        return None;
    }
    let key = (theme::active_index(), lang.to_string(), code.to_string());
    if let Some(hit) = cache().lock().ok()?.get(&key) {
        return Some(hit.clone());
    }
    let (out, resumed) = highlight_resuming(code, lang)?;
    if resumed {
        return Some(out);
    }
    if let Ok(mut cache) = cache().lock() {
        if cache.len() >= CACHE_CAP {
            cache.clear();
        }
        cache.insert(key, out.clone());
    }
    Some(out)
}

/// `code` without the cache, for one-off blocks such as a previewed file.
pub fn block(code: &str, lang: &str) -> Option<Highlighted> {
    if plain(code, lang) {
        return None;
    }
    highlight(code, lang)
}

/// Blocks rendered without highlighting: empty, unlabeled or too large.
fn plain(code: &str, lang: &str) -> bool {
    code.is_empty() || lang.is_empty() || too_large(code)
}

type Cache = Mutex<HashMap<(usize, String, String), Highlighted>>;

fn cache() -> &'static Cache {
    static CACHE: OnceLock<Cache> = OnceLock::new();
    CACHE.get_or_init(Default::default)
}

fn highlight(code: &str, lang: &str) -> Option<Highlighted> {
    let mut progress = Progress::new(lang)?;
    progress.feed(code)?;
    Some(progress.lines)
}

/// Parser position after the lines highlighted so far; feeding more lines continues the block.
#[derive(Clone)]
struct Progress {
    state: ParseState,
    stack: ScopeStack,
    lines: Highlighted,
}

impl Progress {
    fn new(lang: &str) -> Option<Self> {
        Some(Self {
            state: ParseState::new(find_syntax(lang)?),
            stack: ScopeStack::new(),
            lines: Vec::new(),
        })
    }

    fn feed(&mut self, text: &str) -> Option<()> {
        for line in LinesWithEndings::from(text) {
            let ops = self.state.parse_line(line, syntaxes()).ok()?;
            let mut spans: Vec<Span<'static>> = Vec::new();
            let mut start = 0;
            for (offset, op) in ops {
                push_span(&mut spans, &line[start..offset], &self.stack);
                self.stack.apply(&op).ok()?;
                start = offset;
            }
            push_span(&mut spans, &line[start..], &self.stack);
            self.lines.push(spans);
        }
        Some(())
    }
}

/// The latest block's progress through its last complete line, so a streamed block resumes there.
struct Checkpoint {
    key: (usize, String),
    text: String,
    progress: Progress,
}

thread_local! {
    static CHECKPOINT: RefCell<Option<Checkpoint>> = const { RefCell::new(None) };
}

/// Highlight `code`, resuming the checkpoint when `code` extends its text; `true` when it resumed.
fn highlight_resuming(code: &str, lang: &str) -> Option<(Highlighted, bool)> {
    CHECKPOINT.with_borrow_mut(|slot| {
        let key = (theme::active_index(), lang.to_string());
        let (mut progress, done) = match slot.take_if(|c| c.key == key && code.starts_with(&c.text))
        {
            Some(c) => (c.progress, c.text.len()),
            None => (Progress::new(lang)?, 0),
        };
        let complete = code.rfind('\n').map_or(0, |i| i + 1);
        progress.feed(&code[done..complete])?;
        if complete > 0 {
            *slot = Some(Checkpoint {
                key,
                text: code[..complete].to_string(),
                progress: progress.clone(),
            });
        }
        progress.feed(&code[complete..])?;
        Some((progress.lines, done > 0))
    })
}

/// Append `text` styled by the token its innermost scope maps to.
fn push_span(spans: &mut Vec<Span<'static>>, text: &str, stack: &ScopeStack) {
    let text = text.trim_end_matches(['\n', '\r']);
    if text.is_empty() {
        return;
    }
    let style = match palette::token(&stack.scopes, text) {
        Some(tok) => palette::style(tok),
        None => palette::plain(),
    };
    spans.push(Span::styled(text.to_string(), style));
}

#[cfg(test)]
mod tests;
