//! Cached transcript entry heights and cumulative positions.

use std::collections::HashSet;

use super::entry;
use super::viewport::measure_known;
use crate::transcript::Transcript;
use crate::ui::markdown;
use crate::utils::transcript_cache::{
    EntryGeometry, LayoutEntry, TranscriptCache, TranscriptLayout,
};

/// `None` when an uncached layout grows past `limit` rows: it stops there and stores nothing.
pub(super) fn build<'a>(
    cache: &'a mut TranscriptCache,
    markdown_cache: &mut markdown::MarkdownCache,
    expanded: &HashSet<String>,
    transcript: &Transcript,
    width: u16,
    (paused, raw, details): (bool, bool, Option<DetailsContext>),
    limit: u16,
) -> Option<&'a TranscriptLayout> {
    let revision = transcript.revision();
    if cache.layout(revision, width).is_none() {
        let mut top = 0u16;
        let mut entries = Vec::new();
        let mut queue_top = None;
        let mut prompts = Vec::new();
        let mut steers = Vec::new();
        let running = details.is_some_and(|context| context.running);
        let turn_at = |prompt: usize| {
            let mut turns =
                details.map_or_else(Vec::new, |_| transcript.turn_details(prompt, running));
            for turn in &mut turns {
                turn.close = transcript.closing_step(turn, |key| expanded.contains(key));
            }
            turns
        };
        // History still preparing starts mid-turn: that turn's prompt sits above the laid-out rows.
        let start = cache.history_from();
        let mut groups: Vec<_> = (transcript.turn_prompt(start))
            .filter(|_| start > 0 && details.is_some())
            .map(turn_at)
            .unwrap_or_default();
        for entry in transcript.lines_from(start) {
            // A steer continues its turn: it pins under the turn's prompt, not instead of it.
            if transcript.settled_prompt(entry.index).is_some() {
                match entry.steer {
                    true => steers.push(entries.len()),
                    false => {
                        prompts.push(entries.len());
                        groups.extend(turn_at(entry.index));
                    }
                }
            }
            let at = groups.partition_point(|group| group.last < entry.index);
            let group = groups.get(at).filter(|group| group.contains(entry.index));
            let step = group.map(|group| entry::DetailsView {
                details: group,
                expanded: expanded.contains(&group.key),
            });
            if entry.pending && queue_top.is_none() {
                queue_top = Some(top);
            }
            let entry_expanded = expanded.contains(entry.id);
            let group_expanded = entry
                .group
                .as_ref()
                .is_some_and(|group| expanded.contains(&group.key));
            let group_first = entry.group.as_ref().is_some_and(|group| group.first);
            let key = effective_revision(
                entry.rev,
                entry_expanded,
                entry.group.is_some(),
                group_expanded,
                group_first,
                entry.queue_header,
            )
            .wrapping_mul(16)
            .wrapping_add(u64::from(step.is_some()) * 8)
            .wrapping_add(u64::from(step.is_some_and(|step| step.expanded)) * 4)
            .wrapping_add(u64::from(step.is_some_and(|step| step.first(entry.index))) * 2)
            .wrapping_add(u64::from(step.is_some_and(|step| step.last(entry.index))));
            let geometry = cache.geometry(entry.index, key, width, || {
                let rendered = entry::render(
                    &entry,
                    width,
                    entry::ExpansionView {
                        entry: entry_expanded,
                        group: group_expanded,
                        raw,
                        details: step,
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
            if top > limit {
                return None;
            }
        }
        let layout = cache.store_layout(revision, width, top, entries, queue_top, prompts);
        (layout.details, layout.steers) = (groups, steers);
    }
    cache.layout(revision, width)
}

/// Whether turns collapse their details (`collapse_turn_details`), and whether one is running.
#[derive(Clone, Copy)]
pub(super) struct DetailsContext {
    pub running: bool,
}

impl DetailsContext {
    /// The context `app` collapses turns in; a subagent's view keeps them all, its prompts sit in another session.
    pub(super) fn of(app: &crate::app::App) -> Option<Self> {
        (app.view.collapse_turn_details && app.subagents.viewed_subagent_id.is_none()).then_some(
            Self {
                running: matches!(app.session.status, crate::app::Status::Generating { .. }),
            },
        )
    }
}

/// Group membership is keyed too: a neighbor can (un)group an entry without bumping its revision.
fn effective_revision(
    revision: u64,
    entry_expanded: bool,
    grouped: bool,
    group_expanded: bool,
    group_first: bool,
    queue_header: bool,
) -> u64 {
    revision
        .wrapping_mul(32)
        .wrapping_add(u64::from(grouped) * 16)
        .wrapping_add(u64::from(entry_expanded) * 8)
        .wrapping_add(u64::from(group_expanded) * 4)
        .wrapping_add(u64::from(group_first) * 2)
        .wrapping_add(u64::from(queue_header))
}
