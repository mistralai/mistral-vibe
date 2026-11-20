//! Resolve a markdown link target to a local file and the lines it names.

use std::path::{Path, PathBuf};

#[derive(Debug, PartialEq, Eq)]
pub struct Location {
    pub path: PathBuf,
    /// One-based first and last lines the link names.
    pub lines: Option<(usize, usize)>,
    pub kind: Kind,
}

/// How the link names the file: a plain path, a `file://` URL or an editor scheme.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Path,
    FileUrl,
    Editor(&'static str),
}

/// The file `url` points to: editor and `file://` links or plain paths, relative to `cwd`.
pub fn parse(url: &str, cwd: Option<&str>) -> Option<Location> {
    let editor = |scheme: &str| {
        crate::external_url::EDITOR_SCHEMES
            .into_iter()
            .find(|s| scheme.eq_ignore_ascii_case(s))
    };
    let ((path, lines), kind) = match url.split_once("://") {
        Some((scheme, _)) if scheme.eq_ignore_ascii_case("file") => (file_url(url)?, Kind::FileUrl),
        Some((scheme, rest)) => {
            let kind = Kind::Editor(editor(scheme)?);
            let path = rest
                .strip_prefix("file")
                .filter(|path| path.starts_with('/'));
            (linked(path?)?, kind)
        }
        None if opaque_url(url) => return None,
        None => (linked(url)?, Kind::Path),
    };
    if path.is_empty() || path.chars().any(char::is_control) {
        return None;
    }
    let path = Path::new(drive_path(&path));
    let path = match path.is_absolute() {
        true => path.to_owned(),
        false => Path::new(cwd?).join(path),
    };
    Some(Location { path, lines, kind })
}

/// URL schemes without `//` whose targets are never files, even when a number follows the colon.
const OPAQUE_SCHEMES: [&str; 6] = ["mailto", "tel", "sms", "data", "javascript", "urn"];

/// A `scheme:rest` URL such as `mailto:a@b`: not a path, unless the rest is a line number (`main.rs:12`).
fn opaque_url(url: &str) -> bool {
    let Some((scheme, rest)) = url.split_once(':') else {
        return false;
    };
    let is_scheme = scheme.len() > 1
        && scheme.starts_with(|c: char| c.is_ascii_alphabetic())
        && scheme
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c));
    let line = rest.split([':', '#']).next().and_then(range).is_some();
    let known = OPAQUE_SCHEMES
        .iter()
        .any(|known| scheme.eq_ignore_ascii_case(known));
    is_scheme && (known || !line)
}

/// A `file://` URL, its host and escapes resolved by `url`.
fn file_url(url: &str) -> Option<(String, Option<(usize, usize)>)> {
    let parsed = url::Url::parse(url).ok()?;
    let fragment = parsed.fragment().and_then(fragment_lines);
    let path = parsed.to_file_path().ok()?;
    let (path, suffix) = line_suffix(path.to_str()?);
    Some((path.to_owned(), fragment.or(suffix)))
}

/// A path with an optional `#L` fragment or `:line` suffix, percent-decoded.
fn linked(path: &str) -> Option<(String, Option<(usize, usize)>)> {
    let (path, fragment) = path.split_once('#').unwrap_or((path, ""));
    let (path, suffix) = line_suffix(path);
    let path = percent_encoding::percent_decode_str(path)
        .decode_utf8()
        .ok()?;
    Some((path.into_owned(), fragment_lines(fragment).or(suffix)))
}

/// Split `file:12`, `file:12:5` or `file:12-20` into the file and its lines.
fn line_suffix(path: &str) -> (&str, Option<(usize, usize)>) {
    let Some((head, tail)) = path.rsplit_once(':') else {
        return (path, None);
    };
    let Some(lines) = range(tail) else {
        return (path, None);
    };
    match head.rsplit_once(':') {
        Some((file, line)) if !tail.contains('-') => match span(line, line) {
            Some(line) => (file, Some(line)),
            None => (head, Some(lines)),
        },
        _ => (head, Some(lines)),
    }
}

/// GitHub-style `L12` or `L12-L20` fragments.
fn fragment_lines(fragment: &str) -> Option<(usize, usize)> {
    let (start, end) = fragment.split_once('-').unwrap_or((fragment, fragment));
    span(
        start.strip_prefix('L')?,
        end.strip_prefix('L').unwrap_or(end),
    )
}

fn range(text: &str) -> Option<(usize, usize)> {
    let (start, end) = text.split_once('-').unwrap_or((text, text));
    span(start, end)
}

fn span(start: &str, end: &str) -> Option<(usize, usize)> {
    let start: usize = start.parse().ok().filter(|&line| line > 0)?;
    let end: usize = end.parse().ok()?;
    Some((start, end.max(start)))
}

/// `vscode://file/C:/x` carries a slash before the Windows drive.
fn drive_path(path: &str) -> &str {
    match path.as_bytes() {
        [b'/', drive, b':', ..] if drive.is_ascii_alphabetic() => &path[1..],
        _ => path,
    }
}
