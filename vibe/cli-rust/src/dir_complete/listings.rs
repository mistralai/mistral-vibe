//! Cached directory listings and the reads in flight, keyed by directory and read prefix.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use super::Entry;

pub const MAX_CACHED: usize = 8;
pub const MAX_IN_FLIGHT: usize = 4;

/// One read: a whole directory (empty `prefix`), or only its names starting with the lowercased `prefix`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReadKey {
    pub dir: PathBuf,
    pub prefix: String,
}

impl ReadKey {
    pub fn whole(dir: &Path) -> Self {
        Self {
            dir: dir.to_path_buf(),
            prefix: String::new(),
        }
    }
}

/// Entries a read returned; `truncated` when more than the cap matched.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Listing {
    pub entries: Vec<Entry>,
    pub truncated: bool,
}

/// What a query can show now: listings to filter, deduped by name in this order, and a read to start.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Plan {
    pub show: Vec<Arc<Listing>>,
    pub read: Option<ReadKey>,
}

/// Listings cached while a path query is typed, and the reads still running.
#[derive(Default)]
pub struct DirListings {
    cache: Vec<(ReadKey, Option<Arc<Listing>>)>,
    in_flight: Vec<ReadKey>,
    /// Prefix reads that failed, kept out of `cache` so they never evict a listing.
    failed: Vec<ReadKey>,
}

impl DirListings {
    /// Drop the cached listings, so the next path query reads fresh ones.
    pub fn forget(&mut self) {
        self.cache.clear();
        self.failed.clear();
    }

    /// Drop failed prefix reads, so the next keystroke retries them once.
    pub fn retry_failed(&mut self) {
        self.failed.clear();
    }

    /// Listings for `prefix` in `dir`. A truncated directory adds a read of
    /// the names starting with `prefix`, unless a cached or running read
    /// already covers it; until it lands, the closest shorter prefix read shows.
    pub fn plan(&self, dir: &Path, prefix: &str) -> Plan {
        let whole = ReadKey::whole(dir);
        let Some(cached) = self.cached(&whole) else {
            return Plan {
                show: Vec::new(),
                read: Some(whole),
            };
        };
        let Some(whole) = cached else {
            return Plan::default();
        };
        let lower = prefix.to_lowercase();
        if !whole.truncated || lower.is_empty() {
            return Plan {
                show: vec![whole],
                read: None,
            };
        }
        let key = ReadKey {
            dir: dir.to_path_buf(),
            prefix: lower,
        };
        if let Some(filtered) = self.covering(&key) {
            return Plan {
                show: vec![filtered, whole],
                read: None,
            };
        }
        let pending = self
            .in_flight
            .iter()
            .any(|running| running.dir == key.dir && key.prefix.starts_with(&running.prefix));
        let known = self.cached(&key).is_some() || self.failed.contains(&key);
        let read = (!pending && !known).then(|| key.clone());
        Plan {
            show: self.closest(&key).into_iter().chain([whole]).collect(),
            read,
        }
    }

    /// Reserve a slot for `key`; false when it is already being read or every slot is taken.
    pub fn claim(&mut self, key: &ReadKey) -> bool {
        if self.in_flight.contains(key) || self.in_flight.len() >= MAX_IN_FLIGHT {
            return false;
        }
        self.in_flight.push(key.clone());
        true
    }

    /// Free `key`'s slot and cache its result; a failed prefix read is only
    /// remembered until `retry_failed`. Eviction drops prefix reads first,
    /// then whole listings no prefix read depends on.
    pub fn store(&mut self, key: ReadKey, listing: Option<Listing>) {
        self.in_flight.retain(|pending| *pending != key);
        if listing.is_none() && !key.prefix.is_empty() {
            self.failed.push(key);
            return;
        }
        self.cache.retain(|(cached, _)| *cached != key);
        if self.cache.len() >= MAX_CACHED {
            let evict = self
                .cache
                .iter()
                .position(|(cached, _)| !cached.prefix.is_empty())
                .or_else(|| {
                    self.cache
                        .iter()
                        .position(|(cached, _)| !self.needed(&cached.dir, &key))
                });
            self.cache.remove(evict.unwrap_or(0));
        }
        self.cache.push((key, listing.map(Arc::new)));
    }

    /// Whether `dir`'s whole listing backs `key` or a prefix read still running.
    fn needed(&self, dir: &Path, key: &ReadKey) -> bool {
        key.dir == dir
            || self
                .in_flight
                .iter()
                .any(|running| running.dir == dir && !running.prefix.is_empty())
    }

    fn cached(&self, key: &ReadKey) -> Option<Option<Arc<Listing>>> {
        self.cache
            .iter()
            .find(|(cached, _)| cached == key)
            .map(|(_, listing)| listing.clone())
    }

    /// A cached prefix read holding every name of `key`: the same prefix, or a complete shorter one.
    fn covering(&self, key: &ReadKey) -> Option<Arc<Listing>> {
        self.cache.iter().find_map(|(cached, listing)| {
            let listing = listing.as_ref()?;
            let covers = self.is_shorter_read(cached, key)
                && (cached.prefix == key.prefix || !listing.truncated);
            covers.then(|| listing.clone())
        })
    }

    /// The longest cached prefix read under `key`, complete or not.
    fn closest(&self, key: &ReadKey) -> Option<Arc<Listing>> {
        self.cache
            .iter()
            .filter(|(cached, listing)| listing.is_some() && self.is_shorter_read(cached, key))
            .max_by_key(|(cached, _)| cached.prefix.len())
            .and_then(|(_, listing)| listing.clone())
    }

    fn is_shorter_read(&self, cached: &ReadKey, key: &ReadKey) -> bool {
        cached.dir == key.dir && !cached.prefix.is_empty() && key.prefix.starts_with(&cached.prefix)
    }
}
