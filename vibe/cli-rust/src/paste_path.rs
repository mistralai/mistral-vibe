//! Image-path rewriting for pasted text and non-bracketed drag-and-drop input.

use std::path::PathBuf;

const IMAGE_EXTENSIONS: [&str; 5] = ["png", "jpg", "jpeg", "gif", "webp"];
const QUOTES: [char; 2] = ['\'', '"'];
const PATH_ROOTS: [char; 2] = ['/', '~'];
const TOKEN_BOUNDARIES: [char; 3] = ['(', '<', '['];

pub fn maybe_prepend_at_for_image_path(pasted: &str) -> String {
    maybe_prepend_at_for_image_path_on(pasted, cfg!(windows))
}

pub fn maybe_prepend_at_for_image_path_on(pasted: &str, windows: bool) -> String {
    let text = pasted.trim();
    if text.is_empty() || text.contains(['\n', '\r']) {
        return pasted.to_owned();
    }
    let candidate = unescape_spaces(strip_matched_quotes(text));
    if !is_image_path_on(&candidate, windows) {
        return pasted.to_owned();
    }
    image_path_mention_on(&candidate, windows)
}

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
    if !text.contains(['/', '~', '\'', '"', '\\']) {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    let mut pos = 0;
    while pos < text.len() {
        if at_token_boundary(text, pos) {
            if let Some((token, end)) = extract_path_token(text, pos, windows) {
                if is_image_path_on(&token, windows) {
                    out.push('@');
                    out.push_str(&quote_if_needed(&token, windows));
                    pos = end;
                    continue;
                }
            }
        }
        let ch = text[pos..].chars().next().expect("position is in bounds");
        out.push(ch);
        pos += ch.len_utf8();
    }
    out
}

// Existence is validated by prompt preparation; composer rewriting stays I/O-free.
fn is_image_path_on(candidate: &str, windows: bool) -> bool {
    let Some(path) = expanded_path(candidate) else {
        return false;
    };
    (path.is_absolute() || (windows && has_supported_path_root(candidate, true)))
        && path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| {
                IMAGE_EXTENSIONS.contains(&extension.to_ascii_lowercase().as_str())
            })
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

fn unescape_spaces(text: &str) -> String {
    text.replace("\\ ", " ")
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
        return extract_bare(text, pos);
    }
    None
}

fn extract_quoted(text: &str, start: usize, quote: char) -> Option<(String, usize)> {
    let content_start = start + quote.len_utf8();
    let relative_end = text[content_start..].find(quote)?;
    let end = content_start + relative_end;
    Some((text[content_start..end].to_owned(), end + quote.len_utf8()))
}

fn extract_bare(text: &str, start: usize) -> Option<(String, usize)> {
    let mut out = String::new();
    let mut chars = text[start..].char_indices().peekable();
    let mut end = start;
    while let Some((offset, ch)) = chars.next() {
        if ch == '\\' && chars.peek().is_some_and(|(_, next)| *next == ' ') {
            chars.next();
            out.push(' ');
            end = start + offset + 2;
            continue;
        }
        if ch.is_whitespace() {
            break;
        }
        out.push(ch);
        end = start + offset + ch.len_utf8();
    }
    (!out.is_empty()).then_some((out, end))
}
