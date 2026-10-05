//! The update cache repository: `[update_cache]` in `cache.toml` plus the
//! legacy `update_cache.json` migration
//! (Python `adapters/filesystem_update_cache_repository.py`).

use serde_json::Value;

use crate::utils::{cache_store, paths};

const SECTION: &str = "update_cache";
const LEGACY_FILE: &str = "update_cache.json";
/// The section previous Rust-only builds kept the what's-new seen version in.
const LEGACY_WHATS_NEW_SECTION: &str = "whats_new";
const LEGACY_SEEN_KEY: &str = "seen_version";

/// Python `UpdateCache`: what the last update check learned. `source` is the
/// package manager that gave `latest_version` — a reader whose own install
/// answers to a different manager must not treat that version as available.
/// `source_stored_at` binds the tag to the write it belongs to: a writer that
/// updates the entry without knowing the tag (Python's key-merge write) voids
/// the pairing with a sentinel, so only a tag written with the stored
/// timestamp counts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdateCache {
    pub latest_version: String,
    pub stored_at_timestamp: i64,
    pub seen_whats_new_version: Option<String>,
    pub dismissed_version: Option<String>,
    pub source: Option<String>,
    pub source_stored_at: Option<i64>,
}

/// Python `UpdateCacheRepository`: the persisted update cache's get/set.
/// `set` and `modify` carry Python's `OSError` from a write that cannot
/// persist, so a forced check can report it instead of exiting 0.
pub trait UpdateCacheRepository {
    fn get(&self) -> Option<UpdateCache>;
    fn set(&self, cache: &UpdateCache) -> std::io::Result<()>;
    /// Read-modify-write under the repository's lock: the closure sees the
    /// current cache (absent as `None`) and returns the new one, or `None` to
    /// leave the cache absent.
    fn modify(
        &self,
        modify: &mut dyn FnMut(Option<UpdateCache>) -> Option<UpdateCache>,
    ) -> std::io::Result<()> {
        let Some(new) = modify(self.get()) else {
            return Ok(());
        };
        self.set(&new)
    }
}

/// The on-disk repository over `$VIBE_HOME/cache.toml`.
pub struct FileSystemUpdateCacheRepository;

impl UpdateCacheRepository for FileSystemUpdateCacheRepository {
    fn get(&self) -> Option<UpdateCache> {
        if let Some(table) = nonempty_section() {
            return Some(adopt_legacy_seen(parse_table(&table)?));
        }
        let json = read_legacy_json()?;
        if json.is_object() {
            // Python migrates every non-null key; only the known str/int fields
            // have a TOML representation, so the rest are dropped here.
            let _ = cache_store::write_section(SECTION, &legacy_entries(&json));
        }
        Some(adopt_legacy_seen(parse_json(&json)?))
    }

    fn set(&self, cache: &UpdateCache) -> std::io::Result<()> {
        cache_store::write_section(SECTION, &set_entries(cache))
    }

    fn modify(
        &self,
        modify: &mut dyn FnMut(Option<UpdateCache>) -> Option<UpdateCache>,
    ) -> std::io::Result<()> {
        cache_store::modify(|doc| {
            let mut migrated = false;
            let current = current_cache(doc, &mut migrated);
            let Some(new) = modify(current) else {
                return migrated;
            };
            cache_store::insert_section(doc, SECTION, &set_entries(&new));
            true
        })
    }
}

/// The cache a modify starts from, mirroring `get`: a nonempty TOML section
/// is authoritative, an absent or empty one falls back to the legacy JSON —
/// whose keys migrate into the section even when the caller makes no change.
fn current_cache(doc: &mut toml_edit::DocumentMut, migrated: &mut bool) -> Option<UpdateCache> {
    let nonempty = doc
        .get(SECTION)
        .and_then(toml_edit::Item::as_table)
        .filter(|table| !table.is_empty());
    if let Some(table) = nonempty {
        return Some(adopt_legacy_seen_from_doc(parse_table(table)?, doc));
    }
    let json = read_legacy_json()?;
    if json.is_object() {
        cache_store::insert_section(doc, SECTION, &legacy_entries(&json));
        *migrated = true;
    }
    Some(adopt_legacy_seen_from_doc(parse_json(&json)?, doc))
}

/// Previous Rust-only builds stored the seen version in
/// `[whats_new] seen_version`. Adopt it when the shared section has none, so
/// a banner dismissed before the sections merged does not come back. The
/// section is never removed, so an un-persisted adoption is re-derived on
/// every read and settles for good on the next cache write.
fn adopt_legacy_seen(cache: UpdateCache) -> UpdateCache {
    with_legacy_seen(
        cache,
        cache_store::read_string(LEGACY_WHATS_NEW_SECTION, LEGACY_SEEN_KEY),
    )
}

/// The same adoption inside a `cache_store::modify` closure, where the file
/// lock is already held and the legacy value comes from the document in hand.
fn adopt_legacy_seen_from_doc(cache: UpdateCache, doc: &toml_edit::DocumentMut) -> UpdateCache {
    with_legacy_seen(
        cache,
        doc.get(LEGACY_WHATS_NEW_SECTION)
            .and_then(|section| section.get(LEGACY_SEEN_KEY))
            .and_then(toml_edit::Item::as_str)
            .map(str::to_owned),
    )
}

fn with_legacy_seen(cache: UpdateCache, legacy_seen: Option<String>) -> UpdateCache {
    if cache.seen_whats_new_version.is_some() {
        return cache;
    }
    UpdateCache {
        seen_whats_new_version: legacy_seen,
        ..cache
    }
}

fn set_entries(cache: &UpdateCache) -> Vec<(&'static str, cache_store::SectionValue)> {
    let mut entries = vec![
        (
            "latest_version",
            cache_store::SectionValue::Str(cache.latest_version.clone()),
        ),
        (
            "stored_at_timestamp",
            cache_store::SectionValue::Int(cache.stored_at_timestamp),
        ),
    ];
    if let Some(seen) = &cache.seen_whats_new_version {
        entries.push((
            "seen_whats_new_version",
            cache_store::SectionValue::Str(seen.clone()),
        ));
    }
    if let Some(dismissed) = &cache.dismissed_version {
        entries.push((
            "dismissed_version",
            cache_store::SectionValue::Str(dismissed.clone()),
        ));
    }
    if let Some(source) = &cache.source {
        entries.push(("source", cache_store::SectionValue::Str(source.clone())));
        if let Some(stored_at) = cache.source_stored_at {
            entries.push((
                "source_stored_at",
                cache_store::SectionValue::Int(stored_at),
            ));
        }
    }
    entries
}

/// Python `read_section` returns a falsy dict for an absent or empty section,
/// which is what routes `_read_section` to the legacy JSON.
fn nonempty_section() -> Option<toml_edit::Table> {
    cache_store::read_section(SECTION).filter(|table| !table.is_empty())
}

fn read_legacy_json() -> Option<Value> {
    let text = std::fs::read_to_string(legacy_path()?).ok()?;
    serde_json::from_str(&text).ok()
}

fn legacy_path() -> Option<std::path::PathBuf> {
    paths::vibe_home().map(|home| home.join(LEGACY_FILE))
}

fn legacy_entries(json: &Value) -> Vec<(&'static str, cache_store::SectionValue)> {
    let str_field = |key: &'static str| {
        json.get(key)
            .filter(|value| !value.is_null())
            .and_then(Value::as_str)
            .map(|text| (key, cache_store::SectionValue::Str(text.to_owned())))
    };
    let timestamp = json
        .get("stored_at_timestamp")
        .filter(|value| !value.is_null())
        .and_then(Value::as_i64)
        .map(|number| {
            (
                "stored_at_timestamp",
                cache_store::SectionValue::Int(number),
            )
        });
    [
        str_field("latest_version"),
        timestamp,
        str_field("seen_whats_new_version"),
        str_field("dismissed_version"),
    ]
    .into_iter()
    .flatten()
    .collect()
}

/// Python `_parse`: a missing or wrongly typed `latest_version` or
/// `stored_at_timestamp` invalidates the whole cache; optional fields only
/// need to be strings to count. A `source` from a newer writer is kept, and a
/// missing one reads as a legacy PyPI-checked entry.
fn parse_table(table: &toml_edit::Table) -> Option<UpdateCache> {
    let str_field = |key: &str| table.get(key).and_then(toml_edit::Item::as_str);
    Some(UpdateCache {
        latest_version: str_field("latest_version")?.to_owned(),
        stored_at_timestamp: table.get("stored_at_timestamp")?.as_integer()?,
        seen_whats_new_version: str_field("seen_whats_new_version").map(str::to_owned),
        dismissed_version: str_field("dismissed_version").map(str::to_owned),
        source: str_field("source").map(str::to_owned),
        source_stored_at: table
            .get("source_stored_at")
            .and_then(toml_edit::Item::as_integer),
    })
}

fn parse_json(json: &Value) -> Option<UpdateCache> {
    let str_field = |key: &str| json.get(key).and_then(Value::as_str);
    Some(UpdateCache {
        latest_version: str_field("latest_version")?.to_owned(),
        stored_at_timestamp: json.get("stored_at_timestamp")?.as_i64()?,
        seen_whats_new_version: str_field("seen_whats_new_version").map(str::to_owned),
        dismissed_version: str_field("dismissed_version").map(str::to_owned),
        source: str_field("source").map(str::to_owned),
        source_stored_at: json.get("source_stored_at").and_then(Value::as_i64),
    })
}
