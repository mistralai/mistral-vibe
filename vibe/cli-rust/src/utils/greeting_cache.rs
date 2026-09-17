//! `[greeting] last_shown_at` in `~/.vibe/cache.toml`, shared with the Python CLI.

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use toml_edit::{value, DocumentMut, Item, Table};

use super::paths;

const FILE_NAME: &str = "cache.toml";
const SECTION: &str = "greeting";
const KEY: &str = "last_shown_at";
/// Python `_GREETING_INTERVAL_SECONDS`: at most one greeting per 24 hours.
const INTERVAL_SECONDS: i64 = 24 * 60 * 60;

/// Whether the greeting is due, failing open exactly as Python does.
pub fn should_show() -> bool {
    let Some(last_shown) = last_shown() else {
        return true;
    };
    now() - last_shown > INTERVAL_SECONDS
}

fn last_shown() -> Option<i64> {
    read()?.get(SECTION)?.get(KEY)?.as_integer()
}

/// Stamp the greeting as shown, leaving every other cache section untouched.
pub fn mark_shown() {
    let Some(path) = cache_file() else {
        return;
    };
    let mut doc = read().unwrap_or_default();
    let section = doc.entry(SECTION).or_insert(Item::Table(Table::new()));
    let Some(section) = section.as_table_mut() else {
        return;
    };
    section.insert(KEY, value(now()));
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(err) = std::fs::write(&path, doc.to_string()) {
        tracing::debug!(%err, "failed to write greeting cache");
    }
}

fn read() -> Option<DocumentMut> {
    let text = std::fs::read_to_string(cache_file()?).ok()?;
    text.parse::<DocumentMut>().ok()
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs() as i64)
        .unwrap_or(0)
}

fn cache_file() -> Option<PathBuf> {
    paths::vibe_home().map(|home| home.join(FILE_NAME))
}
