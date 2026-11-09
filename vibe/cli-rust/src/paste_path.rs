//! Path-mention rewriting for pasted text and non-bracketed drag-and-drop input.

use std::path::PathBuf;

const IMAGE_EXTENSIONS: [&str; 5] = ["png", "jpg", "jpeg", "gif", "webp"];
const QUOTES: [char; 2] = ['\'', '"'];
const PATH_ROOTS: [char; 2] = ['/', '~'];
const TOKEN_BOUNDARIES: [char; 3] = ['(', '<', '['];
const NARROW_NO_BREAK_SPACE: char = '\u{202f}';

mod list;

pub use list::{
    pasted_image_path, pasted_image_path_on, pasted_image_paths, pasted_image_paths_on,
    path_candidates, path_candidates_on, paths_resolve, paths_resolve_on, MAX_PASTED_PATHS,
};

pub fn has_supported_path_root(candidate: &str, windows: bool) -> bool {
    candidate.starts_with(PATH_ROOTS)
        || (windows && (is_windows_drive_path(candidate) || candidate.starts_with("\\\\")))
}

pub fn image_path_mention(path: &str) -> String {
    image_path_mention_on(path, cfg!(windows))
}

pub fn image_path_mention_on(path: &str, windows: bool) -> String {
    format!("@{}", quote_if_needed(path, windows))
}

pub fn with_image_mention_boundaries(input: &str, cursor: usize, mention: &str) -> String {
    let prefix = input[..cursor]
        .chars()
        .next_back()
        .is_some_and(|character| !character.is_whitespace());
    let suffix = input[cursor..]
        .chars()
        .next()
        .is_none_or(|character| !character.is_whitespace());
    format!(
        "{}{}{}",
        if prefix { " " } else { "" },
        mention,
        if suffix { " " } else { "" },
    )
}

pub fn contains_image_path_mention(text: &str, path: &str) -> bool {
    let mut offset = 0;
    while let Some(relative) = text[offset..].find('@') {
        let anchor = offset + relative;
        let boundary = anchor == 0
            || text[..anchor]
                .chars()
                .next_back()
                .is_some_and(|previous| !previous.is_alphanumeric() && previous != '_');
        if boundary
            && extract_mention_candidate(text, anchor + 1)
                .is_some_and(|candidate| candidate == path)
        {
            return true;
        }
        offset = anchor + 1;
    }
    false
}

/// The image paths of `text`'s `@` mentions, in order.
pub fn image_mentions_in(text: &str) -> Vec<String> {
    text.match_indices('@')
        .filter(|&(anchor, _)| {
            text[..anchor]
                .chars()
                .next_back()
                .is_none_or(|previous| !previous.is_alphanumeric() && previous != '_')
        })
        .filter_map(|(anchor, _)| extract_mention_candidate(text, anchor + 1))
        .filter(|candidate| {
            std::path::Path::new(candidate)
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| {
                    IMAGE_EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str())
                })
        })
        .collect()
}

fn extract_mention_candidate(text: &str, start: usize) -> Option<String> {
    let head = text[start..].chars().next()?;
    if QUOTES.contains(&head) {
        return extract_quoted(text, start, head).map(|(candidate, _)| candidate);
    }
    let candidate: String = text[start..]
        .chars()
        .take_while(|character| is_mention_path_char(*character))
        .collect();
    (!candidate.is_empty()).then_some(candidate)
}

fn is_mention_path_char(character: char) -> bool {
    character.is_alphanumeric() || "._/\\-()[]{}~".contains(character)
}

fn is_windows_drive_path(candidate: &str) -> bool {
    let bytes = candidate.as_bytes();
    bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && matches!(bytes[2], b'\\' | b'/')
}

pub fn rewrite_bare_image_paths_in_text(text: &str) -> String {
    rewrite_bare_image_paths_in_text_on(text, cfg!(windows))
}

pub fn rewrite_bare_image_paths_in_text_on(text: &str, windows: bool) -> String {
    rewrite_bare_image_paths_with_spans_on(text, windows).0
}

/// Rewrite bare image paths, returning each new mention's byte span too.
pub fn rewrite_bare_image_paths_with_spans(text: &str) -> (String, Vec<(usize, usize)>) {
    rewrite_bare_image_paths_with_spans_on(text, cfg!(windows))
}

fn rewrite_bare_image_paths_with_spans_on(
    text: &str,
    windows: bool,
) -> (String, Vec<(usize, usize)>) {
    let mut spans = Vec::new();
    if !text.contains(['/', '~', '\'', '"', '\\']) {
        return (text.to_owned(), spans);
    }
    let mut out = String::with_capacity(text.len());
    let mut pos = 0;
    while pos < text.len() {
        if at_token_boundary(text, pos) {
            if let Some((token, end)) = extract_path_token(text, pos, windows) {
                if is_image_path_on(&token, windows) {
                    let start = out.len();
                    out.push('@');
                    out.push_str(&quote_if_needed(&token, windows));
                    spans.push((start, out.len()));
                    pos = end;
                    continue;
                }
            }
        }
        let ch = text[pos..].chars().next().expect("position is in bounds");
        out.push(ch);
        pos += ch.len_utf8();
    }
    (out, spans)
}

pub fn is_image_path(candidate: &str) -> bool {
    is_image_path_on(candidate, cfg!(windows))
}

// Existence is validated by prompt preparation; composer rewriting stays I/O-free.
fn is_image_path_on(candidate: &str, windows: bool) -> bool {
    rooted_path(candidate, windows).is_some_and(|path| {
        path.extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| {
                IMAGE_EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str())
            })
    })
}

fn rooted_path(candidate: &str, windows: bool) -> Option<PathBuf> {
    expanded_path(candidate)
        .filter(|path| path.is_absolute() || (windows && has_supported_path_root(candidate, true)))
}

fn expanded_path(candidate: &str) -> Option<PathBuf> {
    if candidate == "~" {
        return home_dir();
    }
    if let Some(rest) = candidate.strip_prefix("~/") {
        return Some(home_dir()?.join(rest));
    }
    if candidate.starts_with('~') {
        return None;
    }
    Some(PathBuf::from(candidate))
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn quote_if_needed(path: &str, windows: bool) -> String {
    let needed = (windows && is_windows_drive_path(path))
        || path
            .chars()
            .any(|character| !is_mention_path_char(character));
    match needed {
        true if path.contains('\'') => format!("\"{path}\""),
        true => format!("'{path}'"),
        false => path.to_owned(),
    }
}

fn strip_matched_quotes(text: &str) -> &str {
    let mut chars = text.chars();
    let Some(first) = chars.next() else {
        return text;
    };
    if !QUOTES.contains(&first) || chars.next_back() != Some(first) {
        return text;
    }
    &text[first.len_utf8()..text.len() - first.len_utf8()]
}

fn at_token_boundary(text: &str, pos: usize) -> bool {
    if pos == 0 {
        return true;
    }
    let Some(previous) = text[..pos].chars().next_back() else {
        return true;
    };
    previous != '@' && (previous.is_whitespace() || TOKEN_BOUNDARIES.contains(&previous))
}

fn extract_path_token(text: &str, pos: usize, windows: bool) -> Option<(String, usize)> {
    let head = text[pos..].chars().next()?;
    if QUOTES.contains(&head) {
        return extract_quoted(text, pos, head);
    }
    if PATH_ROOTS.contains(&head) || has_supported_path_root(&text[pos..], windows) {
        return extract_bare(text, pos, windows);
    }
    None
}

fn extract_quoted(text: &str, start: usize, quote: char) -> Option<(String, usize)> {
    let content_start = start + quote.len_utf8();
    let relative_end = text[content_start..].find(quote)?;
    let end = content_start + relative_end;
    Some((text[content_start..end].to_owned(), end + quote.len_utf8()))
}

/// A backslash escapes a space everywhere, and any non-space character on POSIX.
fn is_shell_escape(next: char, windows: bool) -> bool {
    next == ' ' || (!windows && !next.is_whitespace())
}

// Terminals shell-escape dropped paths but leave U+202F (macOS screenshot names) raw.
fn extract_bare(text: &str, start: usize, windows: bool) -> Option<(String, usize)> {
    let mut out = String::new();
    let mut chars = text[start..].char_indices().peekable();
    let mut end = start;
    while let Some((offset, ch)) = chars.next() {
        let escaped = chars
            .peek()
            .filter(|(_, next)| ch == '\\' && is_shell_escape(*next, windows));
        if let Some(&(next_offset, next)) = escaped {
            chars.next();
            out.push(next);
            end = start + next_offset + next.len_utf8();
            continue;
        }
        if ch.is_whitespace() && ch != NARROW_NO_BREAK_SPACE {
            break;
        }
        out.push(ch);
        end = start + offset + ch.len_utf8();
    }
    (!out.is_empty()).then_some((out, end))
}
