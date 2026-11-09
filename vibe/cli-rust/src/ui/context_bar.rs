//! Pure layout of the `/context` bar: the window split into segments, each given
//! a share of the bar's half-cell slots (two per cell, drawn with `▌`).

use crate::server::{ContextBreakdown, ContextCategoryId};

/// One stretch of the context window.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Segment {
    Category(ContextCategoryId),
    /// Measured usage the server has not split yet (a reopened session before
    /// its next model call).
    Unsplit,
    Free,
    Buffer,
}

/// The window's segments in bar order with their token counts: the categories
/// (only once measured, or the unsplit usage), free space up to the compaction
/// threshold, then the auto-compaction buffer. Empty segments are dropped.
pub fn segments(b: &ContextBreakdown) -> Vec<(Segment, u64)> {
    let window = b.context_window;
    let threshold = b.compaction_threshold.min(window);
    let used = if b.measured {
        b.used_tokens.min(window)
    } else {
        0
    };
    let categories = b.categories.iter().filter(|_| b.measured).map(|category| {
        let id = category.id.unwrap_or(ContextCategoryId::Unknown);
        (Segment::Category(id), category.estimated_tokens)
    });
    let unsplit = b.categories.is_empty().then_some((Segment::Unsplit, used));
    categories
        .chain(unsplit)
        .chain([
            (Segment::Free, threshold.saturating_sub(used)),
            (Segment::Buffer, window.saturating_sub(threshold.max(used))),
        ])
        .filter(|(_, tokens)| *tokens > 0)
        .collect()
}

/// Split `slots` proportionally to `weights` (largest remainder), keeping every
/// non-empty weight at least one slot so a tiny category stays visible.
pub fn allocate(weights: &[u64], slots: usize) -> Vec<usize> {
    let total: u128 = weights.iter().map(|w| *w as u128).sum();
    if total == 0 || slots == 0 {
        return vec![0; weights.len()];
    }
    let share = |i: usize| weights[i] as u128 * slots as u128;
    let mut alloc: Vec<usize> = (0..weights.len())
        .map(|i| (share(i) / total) as usize)
        .collect();
    let mut order: Vec<usize> = (0..weights.len()).collect();
    order.sort_by_key(|&i| std::cmp::Reverse(share(i) % total));
    let left = slots - alloc.iter().sum::<usize>();
    for &i in order.iter().take(left) {
        alloc[i] += 1;
    }
    for i in 0..weights.len() {
        if weights[i] == 0 || alloc[i] > 0 {
            continue;
        }
        let largest = (0..alloc.len()).max_by_key(|&j| alloc[j]);
        if let Some(j) = largest.filter(|&j| alloc[j] > 1) {
            alloc[j] -= 1;
            alloc[i] = 1;
        }
    }
    alloc
}

/// The segment covering each of `slots` half-cells, left to right.
pub fn slots(b: &ContextBreakdown, slots: usize) -> Vec<Segment> {
    let segments = segments(b);
    let weights: Vec<u64> = segments.iter().map(|(_, tokens)| *tokens).collect();
    let alloc = allocate(&weights, slots);
    let mut out: Vec<Segment> = segments
        .iter()
        .zip(alloc)
        .flat_map(|((segment, _), n)| std::iter::repeat_n(*segment, n))
        .collect();
    out.resize(slots, Segment::Free);
    out
}

/// The cell where the auto-compaction buffer starts, for the threshold mark.
pub fn buffer_start(slots: &[Segment]) -> Option<usize> {
    slots
        .iter()
        .position(|segment| *segment == Segment::Buffer)
        .map(|slot| slot / 2)
}

/// `tokens` as a rounded percent of the window.
pub fn percent(tokens: u64, window: u64) -> u64 {
    let window = window.max(1) as u128;
    ((tokens as u128 * 100 + window / 2) / window) as u64
}
