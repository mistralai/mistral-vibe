//! The release-notes history: one markdown body per version, newest first.

use std::path::Path;

use crate::update_notifier::version::parse_version;

/// The embedded `vibe/whats_new/<version>.md` bodies, newest first.
const EMBEDDED: &[(&str, &str)] = include!(concat!(env!("OUT_DIR"), "/whats_new.rs"));
/// Test-only: read the history from this directory instead of the embedded one.
const DIR_ENV: &str = "VIBE_WHATS_NEW_DIR";
/// Releases the startup banner lists before pointing at `/release-notes` for the rest.
pub const MAX_STARTUP_ENTRIES: usize = 5;
/// The startup banner's title, drawn in its frame.
pub const TITLE: &str = "Release notes";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub version: String,
    pub body: String,
}

/// Every release with notes, newest first.
pub fn entries() -> Vec<Entry> {
    match std::env::var_os(DIR_ENV) {
        Some(dir) => read_dir(Path::new(&dir)),
        None => EMBEDDED
            .iter()
            .filter_map(|(version, body)| entry(version, body))
            .collect(),
    }
}

fn entry(version: &str, body: &str) -> Option<Entry> {
    let body = body.trim();
    (!body.is_empty()).then(|| Entry {
        version: version.to_owned(),
        body: body.to_owned(),
    })
}

/// The `<version>.md` files of `dir`, newest first; anything else is skipped.
fn read_dir(dir: &Path) -> Vec<Entry> {
    let mut entries: Vec<Entry> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|file| {
            let name = file.file_name().into_string().ok()?;
            let version = name.strip_suffix(".md")?;
            parse_version(version)?;
            entry(version, &std::fs::read_to_string(file.path()).ok()?)
        })
        .collect();
    entries.sort_by_cached_key(|entry| std::cmp::Reverse(parse_version(&entry.version)));
    entries
}

/// The releases newer than `seen`, newest first; only the newest when `seen` is not a version.
pub fn unseen<'a>(entries: &'a [Entry], seen: Option<&str>) -> &'a [Entry] {
    let Some(seen) = seen.and_then(parse_version) else {
        return &entries[..entries.len().min(1)];
    };
    let newer = entries
        .iter()
        .take_while(|entry| parse_version(&entry.version).is_some_and(|version| version > seen))
        .count();
    &entries[..newer]
}

/// Whether a rendered banner line is a release heading, `vX.Y.Z` alone.
pub fn is_version_heading(line: &str) -> bool {
    line.strip_prefix('v')
        .is_some_and(|version| version.split('.').count() == 3 && parse_version(version).is_some())
}

/// The startup banner body: the newest unseen releases up to the cap, then a `/release-notes` pointer.
pub fn startup_body(unseen: &[Entry]) -> Option<String> {
    if unseen.is_empty() {
        return None;
    }
    let shown = unseen.len().min(MAX_STARTUP_ENTRIES);
    let sections: Vec<String> = unseen[..shown]
        .iter()
        .map(|entry| format!("**v{}**\n\n{}", entry.version, entry.body))
        .collect();
    let footer = match unseen.len() - shown {
        0 => "_Browse every release with `/release-notes`._".to_owned(),
        1 => {
            "_1 earlier release not shown: browse every release with `/release-notes`._".to_owned()
        }
        hidden => format!(
            "_{hidden} earlier releases not shown: browse every release with `/release-notes`._"
        ),
    };
    Some(format!("{}\n\n{footer}", sections.join("\n\n")))
}

/// The `/release-notes` document: the releases whose version or notes contain `query`, newest first.
pub fn release_notes(entries: &[Entry], query: &str) -> String {
    let query = caseless::default_case_fold_str(query.trim());
    let sections: Vec<String> = entries
        .iter()
        .filter(|entry| {
            query.is_empty()
                || caseless::default_case_fold_str(&format!("v{} {}", entry.version, entry.body))
                    .contains(&query)
        })
        .map(|entry| format!("### v{}\n\n{}", entry.version, entry.body))
        .collect();
    sections.join("\n\n")
}
