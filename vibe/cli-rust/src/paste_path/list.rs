//! Pasted path lists (Finder copy, drag-and-drop) turned into `@` mentions.

use std::path::Path;

use super::{
    extract_path_token, has_supported_path_root, is_image_path_on, is_shell_escape, rooted_path,
    strip_matched_quotes,
};

/// Longer pasted path lists stay raw text.
pub const MAX_PASTED_PATHS: usize = 100;

/// The image path a paste consists of, when it is a single image path.
pub fn pasted_image_path(pasted: &str) -> Option<String> {
    pasted_image_path_on(pasted, cfg!(windows))
}

pub fn pasted_image_path_on(pasted: &str, windows: bool) -> Option<String> {
    pasted_image_paths_on(pasted, windows)
        .filter(|paths| paths.len() == 1)
        .and_then(|mut paths| paths.pop())
}

/// The image paths a paste consists of, when it lists image paths only.
pub fn pasted_image_paths(pasted: &str) -> Option<Vec<String>> {
    pasted_image_paths_on(pasted, cfg!(windows))
}

pub fn pasted_image_paths_on(pasted: &str, windows: bool) -> Option<Vec<String>> {
    let paths = path_candidates_on(pasted, windows);
    let images = !paths.is_empty()
        && paths
            .iter()
            .all(|path| has_segment(path) && is_image_path_on(path, windows));
    images.then_some(paths)
}

pub fn path_candidates(pasted: &str) -> Vec<String> {
    path_candidates_on(pasted, cfg!(windows))
}

/// Paths of a pasted list: shell-escaped tokens, else one path per line; empty when not a list.
pub fn path_candidates_on(pasted: &str, windows: bool) -> Vec<String> {
    let text = pasted.trim();
    split_path_tokens(text, windows)
        .or_else(|| {
            text.lines()
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .map(|line| {
                    let unquoted = strip_matched_quotes(line);
                    let candidate = match unquoted.len() == line.len() {
                        true => unescape(line, windows),
                        false => unquoted.to_owned(),
                    };
                    has_supported_path_root(&candidate, windows).then_some(candidate)
                })
                .collect()
        })
        .filter(|paths| paths.len() <= MAX_PASTED_PATHS)
        .unwrap_or_default()
}

pub fn paths_resolve(paths: &[String], exists: impl Fn(&Path) -> bool) -> bool {
    paths_resolve_on(paths, cfg!(windows), exists)
}

/// Whether every path is an image or exists; false for an empty list.
pub fn paths_resolve_on(paths: &[String], windows: bool, exists: impl Fn(&Path) -> bool) -> bool {
    !paths.is_empty()
        && paths.iter().all(|path| {
            has_segment(path)
                && (is_image_path_on(path, windows)
                    || rooted_path(path, windows).is_some_and(|resolved| exists(&resolved)))
        })
}

// Bare roots like `/`, `//` (a comment marker) or `~` are never mentions.
fn has_segment(path: &str) -> bool {
    !matches!(path.trim_end_matches(['/', '\\']), "" | "~")
}

fn split_path_tokens(text: &str, windows: bool) -> Option<Vec<String>> {
    let mut paths = Vec::new();
    let mut pos = 0;
    loop {
        pos = text.len() - text[pos..].trim_start().len();
        if pos == text.len() {
            return Some(paths);
        }
        let (token, end) = extract_path_token(text, pos, windows)?;
        let separated = text[end..].chars().next().is_none_or(char::is_whitespace);
        if !separated || !has_supported_path_root(&token, windows) {
            return None;
        }
        paths.push(token);
        pos = end;
    }
}

fn unescape(text: &str, windows: bool) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        match chars.peek() {
            Some(&next) if ch == '\\' && is_shell_escape(next, windows) => {
                out.push(next);
                chars.next();
            }
            _ => out.push(ch),
        }
    }
    out
}
