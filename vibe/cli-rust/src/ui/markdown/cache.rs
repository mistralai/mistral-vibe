//! Bounded prepared-markdown cache keyed by entry revision, width, and theme.

use std::collections::VecDeque;
use std::sync::Arc;

use super::PreparedMarkdown;

pub const MAX_MARKDOWN_CACHE_ENTRIES: usize = 64;
pub const MAX_MARKDOWN_CACHE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone, Copy, PartialEq, Eq)]
struct Key {
    entry: usize,
    revision: u64,
    width: u16,
    theme: usize,
}

struct Cached {
    key: Key,
    bytes: usize,
    prepared: Arc<PreparedMarkdown>,
}

#[derive(Default)]
pub struct MarkdownCache {
    entries: VecDeque<Cached>,
    bytes: usize,
}

impl MarkdownCache {
    pub fn prepare(
        &mut self,
        entry: usize,
        revision: u64,
        width: u16,
        theme: usize,
        source: impl FnOnce() -> String,
    ) -> Arc<PreparedMarkdown> {
        let key = Key {
            entry,
            revision,
            width,
            theme,
        };
        if let Some(index) = self.entries.iter().position(|cached| cached.key == key) {
            let cached = self.entries.remove(index).expect("cached index exists");
            let prepared = Arc::clone(&cached.prepared);
            self.entries.push_back(cached);
            return prepared;
        }

        self.remove_stale(entry, width);
        let source = source();
        let prepared = Arc::new(super::prepare_uncached(&source, width));
        let bytes = prepared.retained_bytes();
        if bytes > MAX_MARKDOWN_CACHE_BYTES {
            return prepared;
        }
        while self.entries.len() >= MAX_MARKDOWN_CACHE_ENTRIES
            || self.bytes.saturating_add(bytes) > MAX_MARKDOWN_CACHE_BYTES
        {
            let Some(evicted) = self.entries.pop_front() else {
                break;
            };
            self.bytes = self.bytes.saturating_sub(evicted.bytes);
        }
        self.bytes += bytes;
        self.entries.push_back(Cached {
            key,
            bytes,
            prepared: Arc::clone(&prepared),
        });
        prepared
    }

    fn remove_stale(&mut self, entry: usize, width: u16) {
        let mut kept = VecDeque::with_capacity(self.entries.len());
        while let Some(cached) = self.entries.pop_front() {
            if cached.key.entry == entry && cached.key.width == width {
                self.bytes = self.bytes.saturating_sub(cached.bytes);
            } else {
                kept.push_back(cached);
            }
        }
        self.entries = kept;
    }
}
