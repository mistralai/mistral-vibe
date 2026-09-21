//! Syntax highlighting: syntect grammars painted with Textual's token palette.

mod palette;

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
/// Highlighted blocks kept per theme; the transcript re-renders them every
/// frame, and diffs enter one line at a time.
const CACHE_CAP: usize = 512;

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
    if code.is_empty() || lang.is_empty() || too_large(code) {
        return None;
    }
    let key = (theme::active_index(), lang.to_string(), code.to_string());
    if let Some(hit) = cache().lock().ok()?.get(&key) {
        return Some(hit.clone());
    }
    let out = highlight(code, lang)?;
    if let Ok(mut cache) = cache().lock() {
        if cache.len() >= CACHE_CAP {
            cache.clear();
        }
        cache.insert(key, out.clone());
    }
    Some(out)
}

type Cache = Mutex<HashMap<(usize, String, String), Highlighted>>;

fn cache() -> &'static Cache {
    static CACHE: OnceLock<Cache> = OnceLock::new();
    CACHE.get_or_init(Default::default)
}

fn highlight(code: &str, lang: &str) -> Option<Highlighted> {
    let syntax = find_syntax(lang)?;
    let mut state = ParseState::new(syntax);
    let mut stack = ScopeStack::new();
    let mut out = Vec::new();
    for line in LinesWithEndings::from(code) {
        let ops = state.parse_line(line, syntaxes()).ok()?;
        let mut spans: Vec<Span<'static>> = Vec::new();
        let mut start = 0;
        for (offset, op) in ops {
            push_span(&mut spans, &line[start..offset], &stack);
            stack.apply(&op).ok()?;
            start = offset;
        }
        push_span(&mut spans, &line[start..], &stack);
        out.push(spans);
    }
    Some(out)
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
