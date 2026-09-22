//! Cached transcript entry heights and cumulative positions.

use std::collections::HashSet;

use super::entry;
use super::viewport::measure_known;
use crate::transcript::Transcript;
use crate::ui::markdown;
use crate::utils::transcript_cache::{
    EntryGeometry, LayoutEntry, TranscriptCache, TranscriptLayout,
};

pub(super) fn build<'a>(
    cache: &'a mut TranscriptCache,
    markdown_cache: &mut markdown::MarkdownCache,
    expanded: &HashSet<String>,
    transcript: &Transcript,
    width: u16,
    paused: bool,
) -> &'a TranscriptLayout {
    let revision = transcript.revision();
    if cache.layout(revision, width).is_none() {
        let mut top = 0u16;
        let mut entries = Vec::new();
        for entry in transcript.lines() {
            let entry_expanded = expanded.contains(entry.id);
            let group_expanded = entry
                .group
                .as_ref()
                .is_some_and(|group| expanded.contains(&group.key));
            let group_first = entry.group.as_ref().is_some_and(|group| group.first);
            let key = effective_revision(
                entry.rev,
                entry_expanded,
                group_expanded,
                group_first,
                entry.queue_header,
            );
            let geometry = cache.geometry(entry.index, key, width, || {
                let rendered = entry::render(
                    &entry,
                    width,
                    entry::ExpansionView {
                        entry: entry_expanded,
                        group: group_expanded,
                    },
                    0,
                    entry::QueueView {
                        paused,
                        ..entry::QueueView::default()
                    },
                    false,
                    Some(markdown_cache),
                );
                let prewrapped = rendered.prewrapped(width);
                EntryGeometry {
                    height: measure_known(rendered.lines(), width, prewrapped),
                    prewrapped,
                }
            });
            entries.push(LayoutEntry {
                index: entry.index,
                top,
                height: geometry.height,
                prewrapped: geometry.prewrapped,
            });
            top = top.saturating_add(geometry.height);
        }
        cache.store_layout(revision, width, top, entries);
    }
    cache.layout(revision, width).expect("layout was cached")
}

fn effective_revision(
    revision: u64,
    entry_expanded: bool,
    group_expanded: bool,
    group_first: bool,
    queue_header: bool,
) -> u64 {
    revision
        .wrapping_mul(16)
        .wrapping_add(u64::from(entry_expanded) * 8)
        .wrapping_add(u64::from(group_expanded) * 4)
        .wrapping_add(u64::from(group_first) * 2)
        .wrapping_add(u64::from(queue_header))
}
