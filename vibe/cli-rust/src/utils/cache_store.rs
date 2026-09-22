//! `cache.toml` section reads and writes under `VIBE_HOME`, shared with the
//! Python CLI (Python `FileSystemCacheStore`); one section is left untouched by
//! another's write.

use toml_edit::{value, DocumentMut, Item, Table};

use super::paths;

const FILE_NAME: &str = "cache.toml";

fn read() -> Option<DocumentMut> {
    let text = std::fs::read_to_string(cache_file()?).ok()?;
    text.parse::<DocumentMut>().ok()
}

/// The store a write starts from: an absent file opens a fresh document, while
/// one that exists but does not parse yields `None` so the write is skipped
/// instead of rewriting the file without its unparsed sections.
fn read_for_write() -> Option<DocumentMut> {
    let path = cache_file()?;
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Some(DocumentMut::new()),
        Err(err) => {
            tracing::warn!(%err, "failed to read cache.toml; skipping write");
            return None;
        }
    };
    match text.parse::<DocumentMut>() {
        Ok(doc) => Some(doc),
        Err(err) => {
            tracing::warn!(%err, "cache.toml is malformed; skipping write");
            None
        }
    }
}

fn cache_file() -> Option<std::path::PathBuf> {
    paths::vibe_home().map(|home| home.join(FILE_NAME))
}

fn section_mut<'a>(doc: &'a mut DocumentMut, name: &str) -> Option<&'a mut Table> {
    doc.entry(name)
        .or_insert(Item::Table(Table::new()))
        .as_table_mut()
}

/// The string one section key holds, or `None` when file, section, or key is absent.
pub fn read_string(section: &str, key: &str) -> Option<String> {
    read()?.get(section)?.get(key)?.as_str().map(str::to_owned)
}

/// The integer one section key holds, or `None` when it is not one.
pub fn read_int(section: &str, key: &str) -> Option<i64> {
    read()?.get(section)?.get(key)?.as_integer()
}

/// The strings of one section key's array, or `None` when it is not an array.
pub fn read_string_list(section: &str, key: &str) -> Option<Vec<String>> {
    let doc = read()?;
    let items = doc.get(section)?.get(key)?.as_array()?;
    Some(
        items
            .iter()
            .filter_map(|item| item.as_str().map(str::to_owned))
            .collect(),
    )
}

/// Write one section key, preserving every other section and key.
pub fn write_string(section: &str, key: &str, new_value: &str) {
    let Some(mut doc) = read_for_write() else {
        return;
    };
    if let Some(table) = section_mut(&mut doc, section) {
        table.insert(key, value(new_value));
        store(&doc);
    }
}

/// Write one section's integer key, preserving every other section and key.
pub fn write_int(section: &str, key: &str, new_value: i64) {
    let Some(mut doc) = read_for_write() else {
        return;
    };
    if let Some(table) = section_mut(&mut doc, section) {
        table.insert(key, value(new_value));
        store(&doc);
    }
}

/// Write one section's string-array key, preserving every other section and key.
pub fn write_string_list(section: &str, key: &str, new_value: &[String]) {
    let Some(mut doc) = read_for_write() else {
        return;
    };
    if let Some(table) = section_mut(&mut doc, section) {
        let mut array = toml_edit::Array::new();
        for item in new_value {
            array.push(item.as_str());
        }
        table.insert(key, value(array));
        store(&doc);
    }
}

fn store(doc: &DocumentMut) {
    let Some(path) = cache_file() else {
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(err) = std::fs::write(&path, doc.to_string()) {
        tracing::debug!(%err, "failed to write cache.toml");
    }
}
