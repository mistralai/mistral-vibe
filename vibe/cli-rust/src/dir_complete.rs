//! `@` completion of rooted paths (`/`, `~/`, `./`, `../`): one directory listed on demand.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use crate::app::App;
use crate::utils::fuzzy;

mod listings;

pub use listings::{DirListings, Listing, Plan, ReadKey, MAX_CACHED, MAX_IN_FLIGHT};

/// Entries kept per read, so `/nix/store` or `/usr/lib` stay cheap to filter;
/// a directory past it is read again filtered by the typed prefix.
pub const MAX_ENTRIES: usize = 5_000;
/// A read still running after this (a dead network mount) lists nothing and frees its slot.
const READ_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(2);
const ROOTS: [&str; 4] = ["/", "~/", "./", "../"];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub name: String,
    pub is_dir: bool,
}

/// A finished read; `None` when the directory is missing, unreadable or timed out.
pub struct Listed {
    pub key: ReadKey,
    pub listing: Option<Listing>,
}

/// The typed directory (through its last `/`) and the name prefix after it.
pub fn split(query: &str) -> Option<(&str, &str)> {
    split_on(query, cfg!(windows))
}

/// `split`, with Windows drive paths (`C:/`) rooted when `windows`.
pub fn split_on(query: &str, windows: bool) -> Option<(&str, &str)> {
    let rooted = ROOTS.iter().any(|root| query.starts_with(root))
        || (windows && crate::paste_path::is_windows_drive_path(query));
    let cut = query.rfind('/')? + 1;
    rooted.then(|| query.split_at(cut))
}

/// Where a typed directory lives: `~/` under the home, relative ones under `cwd`.
pub fn resolve(dir: &str, cwd: Option<&str>) -> Option<PathBuf> {
    if let Some(rest) = dir.strip_prefix("~/") {
        return Some(crate::paste_path::home_dir()?.join(rest));
    }
    Some(cwd.map_or_else(|| PathBuf::from(dir), |cwd| Path::new(cwd).join(dir)))
}

/// Labels for a rooted path query, `None` for any other query: the typed
/// directory itself first when nothing follows its `/`, then its matching
/// entries. A directory not listed yet, or missing, shows nothing.
pub fn entries(app: &mut App, query: &str, limit: usize) -> Option<Vec<String>> {
    let Some((typed, prefix)) = split(query) else {
        app.completion.dirs.forget();
        return None;
    };
    let Some(dir) = resolve(typed, app.session.cwd.as_deref()) else {
        return Some(Vec::new());
    };
    let Plan { show, read } = app.completion.dirs.plan(&dir, prefix);
    if let Some(key) = read {
        request(app, key);
    }
    if show.is_empty() {
        return Some(Vec::new());
    }
    let mut seen = HashSet::new();
    let entries = show
        .iter()
        .flat_map(|listing| &listing.entries)
        .filter(|entry| seen.insert(entry.name.as_str()));
    let current = prefix.is_empty().then(|| format!("@{typed}"));
    let rest = limit.saturating_sub(usize::from(current.is_some()));
    Some(
        current
            .into_iter()
            .chain(matching(entries, typed, prefix, rest))
            .collect(),
    )
}

/// Entries matching `prefix`, best first, as `@` labels under `typed`;
/// hidden entries only for a `.` prefix.
pub fn matching<'a>(
    entries: impl IntoIterator<Item = &'a Entry>,
    typed: &str,
    prefix: &str,
    limit: usize,
) -> Vec<String> {
    let hidden = prefix.starts_with('.');
    let mut scored: Vec<(i64, &Entry)> = entries
        .into_iter()
        .filter(|entry| hidden || !entry.name.starts_with('.'))
        .filter_map(|entry| Some((fuzzy::score(prefix, &entry.name)?, entry)))
        .collect();
    scored.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.name.cmp(&b.1.name)));
    scored
        .into_iter()
        .take(limit)
        .map(|(_, entry)| {
            let slash = if entry.is_dir { "/" } else { "" };
            format!("@{typed}{}{slash}", entry.name)
        })
        .collect()
}

/// Read up to `MAX_ENTRIES` entries of `dir` whose lowercased name starts
/// with `prefix`; `None` when it is missing or unreadable.
pub fn list(dir: &Path, prefix: &str) -> Option<Listing> {
    list_capped(dir, prefix, MAX_ENTRIES)
}

/// `list` keeping at most `cap` entries, `truncated` when more matched.
pub fn list_capped(dir: &Path, prefix: &str, cap: usize) -> Option<Listing> {
    let read = std::fs::read_dir(dir).ok()?;
    let mut entries: Vec<Entry> = read
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            if !prefix.is_empty() && !name.to_lowercase().starts_with(prefix) {
                return None;
            }
            let kind = entry.file_type().ok()?;
            let is_dir = kind.is_dir()
                || (kind.is_symlink() && std::fs::metadata(entry.path()).is_ok_and(|m| m.is_dir()));
            Some(Entry { name, is_dir })
        })
        .take(cap + 1)
        .collect();
    let truncated = entries.len() > cap;
    entries.truncate(cap);
    Some(Listing { entries, truncated })
}

/// Cache a finished read and refresh the popup that waited for it.
pub fn apply_event(app: &mut App, Listed { key, listing }: Listed) {
    app.completion.dirs.store(key, listing);
    crate::completion_manager::refresh(app);
}

/// Run `key`'s read on a blocking job, at most `MAX_IN_FLIGHT` at once so hung mounts never pile up reads.
fn request(app: &mut App, key: ReadKey) {
    if app.events.is_none() || !app.completion.dirs.claim(&key) {
        return;
    }
    crate::ui_event::spawn(app, async move {
        let read = key.clone();
        let job = crate::work::spawn_blocking(move || list(&read.dir, &read.prefix));
        let listing = tokio::select! {
            listing = job => listing.ok().flatten(),
            () = crate::clock::sleep_real(READ_TIMEOUT) => None,
        };
        Listed { key, listing }
    });
}
