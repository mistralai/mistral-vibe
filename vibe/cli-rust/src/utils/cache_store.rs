//! `cache.toml` section reads and writes under `VIBE_HOME`, shared with the
//! Python CLI (Python `FileSystemCacheStore`); one section is left untouched by
//! another's write.

use std::sync::{Mutex, MutexGuard, OnceLock};

use toml_edit::{value, DocumentMut, Item, Table};

use super::paths;

const FILE_NAME: &str = "cache.toml";

/// One section key's value, mirroring Python's mixed str/int `write_section` payloads.
pub enum SectionValue {
    Str(String),
    Int(i64),
}

/// Python's `_FILE_LOCK`: several subsystems read and write different sections
/// of the same file, so the whole-file access is serialized per process.
fn file_lock() -> MutexGuard<'static, ()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn read() -> Option<DocumentMut> {
    // Python's `read_section` holds the lock across the read.
    let _lock = file_lock();
    let text = std::fs::read_to_string(cache_file()?).ok()?;
    text.parse::<DocumentMut>().ok()
}

/// The store a write starts from: an absent file opens a fresh document, while
/// one that exists but does not parse fails the write instead of rewriting the
/// file without its unparsed sections.
fn read_for_write() -> std::io::Result<DocumentMut> {
    let path = cache_file().ok_or_else(|| std::io::Error::other("no Vibe home"))?;
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(DocumentMut::new()),
        Err(err) => {
            tracing::warn!(%err, "failed to read cache.toml; skipping write");
            return Err(err);
        }
    };
    match text.parse::<DocumentMut>() {
        Ok(doc) => Ok(doc),
        Err(err) => {
            tracing::warn!(%err, "cache.toml is malformed; skipping write");
            Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "cache.toml is malformed",
            ))
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

/// One section's table, or `None` when file, section, or table is absent.
pub fn read_section(section: &str) -> Option<Table> {
    read()?.get(section)?.as_table().cloned()
}

/// Merge several keys of one section into an in-memory document, preserving
/// every other section and key (Python `CacheStore.write_section`). Optional
/// keys are simply omitted from `entries` by the caller, so an omitted key
/// keeps its stored value.
pub fn insert_section(doc: &mut DocumentMut, section: &str, entries: &[(&str, SectionValue)]) {
    if let Some(table) = section_mut(doc, section) {
        for (key, new_value) in entries {
            match new_value {
                SectionValue::Str(text) => {
                    table.insert(key, value(text));
                }
                SectionValue::Int(number) => {
                    table.insert(key, value(*number));
                }
            }
        }
    }
}

/// Write several keys of one section, preserving every other section and key.
pub fn write_section(section: &str, entries: &[(&str, SectionValue)]) -> std::io::Result<()> {
    let _lock = file_lock();
    let mut doc = read_for_write()?;
    insert_section(&mut doc, section, entries);
    store(&doc)
}

/// Read-modify-write of the whole file under the lock — unlike paired
/// `read_section`/`write_section` calls, no other writer can interleave. The
/// closure sees the current document and returns whether to persist it.
pub fn modify<F>(modify: F) -> std::io::Result<()>
where
    F: FnOnce(&mut DocumentMut) -> bool,
{
    let _lock = file_lock();
    let mut doc = read_for_write()?;
    if modify(&mut doc) {
        store(&doc)?;
    }
    Ok(())
}

/// Write one section key, preserving every other section and key.
pub fn write_string(section: &str, key: &str, new_value: &str) {
    let _ = write_section(section, &[(key, SectionValue::Str(new_value.to_owned()))]);
}

/// Write one section's integer key, preserving every other section and key.
pub fn write_int(section: &str, key: &str, new_value: i64) {
    let _ = write_section(section, &[(key, SectionValue::Int(new_value))]);
}

/// Write one section's string-array key, preserving every other section and key.
pub fn write_string_list(section: &str, key: &str, new_value: &[String]) {
    let _lock = file_lock();
    let mut doc = match read_for_write() {
        Ok(doc) => doc,
        Err(_) => return,
    };
    if let Some(table) = section_mut(&mut doc, section) {
        let mut array = toml_edit::Array::new();
        for item in new_value {
            array.push(item.as_str());
        }
        table.insert(key, value(array));
        let _ = store(&doc);
    }
}

fn store(doc: &DocumentMut) -> std::io::Result<()> {
    let path = cache_file().ok_or_else(|| std::io::Error::other("no Vibe home"))?;
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(err) = std::fs::write(&path, doc.to_string()) {
        tracing::debug!(%err, "failed to write cache.toml");
        return Err(err);
    }
    Ok(())
}
