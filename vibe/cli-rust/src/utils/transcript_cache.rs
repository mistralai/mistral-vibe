//! Cached transcript heights and cumulative positions for fast viewport lookup.

use crate::transcript::Transcript;

#[derive(Clone, Copy)]
pub struct EntryGeometry {
    pub height: u16,
    pub prewrapped: bool,
}

#[derive(Default)]
struct EntryHeights {
    rev: u64,
    by_width: [Option<EntryGeometry>; 2],
}

pub struct LayoutEntry {
    pub index: usize,
    pub top: u16,
    pub height: u16,
    pub prewrapped: bool,
}

#[derive(Default)]
pub struct TranscriptLayout {
    revision: u64,
    pub height: u16,
    pub entries: Vec<LayoutEntry>,
    /// Layout row of the first queued prompt, if any.
    pub queue_top: Option<u16>,
}

impl TranscriptLayout {
    /// The blank rows that push the queued run down by `free` rows, to the viewport bottom.
    pub fn queue_spacer(&self, free: u16) -> QueueSpacer {
        self.queue_top
            .map_or_else(QueueSpacer::default, |at| QueueSpacer { at, height: free })
    }
}

/// Blank rows inserted at layout row `at`, above the queued run.
#[derive(Clone, Copy, Default)]
pub struct QueueSpacer {
    pub at: u16,
    pub height: u16,
}

impl QueueSpacer {
    /// Shift a layout row at or below the spacer past it.
    pub fn offset(self, top: u16) -> u16 {
        match top >= self.at {
            true => top.saturating_add(self.height),
            false => top,
        }
    }
}

#[derive(Default)]
pub struct TranscriptCache {
    history_from: Option<usize>,
    heights: Vec<EntryHeights>,
    layouts: [TranscriptLayout; 2],
    widths: (u16, u16),
    theme: usize,
    queue_paused: bool,
}

impl TranscriptCache {
    pub fn start_history(&mut self, entries: usize) {
        self.history_from = Some(entries);
        self.invalidate_layouts();
    }

    /// Keep cached heights on their shifted entries and prepare `count` prepended ones.
    pub fn start_older_history(&mut self, count: usize) {
        self.heights.splice(
            0..0,
            std::iter::repeat_with(EntryHeights::default).take(count),
        );
        self.start_history(count);
    }

    pub fn history_from(&self) -> usize {
        self.history_from.unwrap_or(0)
    }

    pub fn preparing_history(&self) -> bool {
        self.history_from.is_some()
    }

    /// Advance automatically each frame; no scroll input or additional RPC is needed.
    pub fn advance_history(&mut self, transcript: &Transcript) -> bool {
        let Some(end) = self.history_from else {
            return false;
        };
        if end == 0 {
            self.history_from = None;
            return false;
        }
        self.history_from = Some(transcript.history_batch_start(end));
        self.invalidate_layouts();
        true
    }

    /// Drop cached geometry when width, theme, or queue-header copy changes.
    pub fn ensure_context(&mut self, full: u16, content: u16, theme: usize, queue_paused: bool) {
        let context_changed = self.widths != (full, content)
            || self.theme != theme
            || self.queue_paused != queue_paused;
        if context_changed {
            self.heights.clear();
            self.invalidate_layouts();
            self.widths = (full, content);
            self.theme = theme;
            self.queue_paused = queue_paused;
        }
    }

    /// Cached height and wrapping mode for an ordered entry revision.
    pub fn geometry(
        &mut self,
        index: usize,
        rev: u64,
        width: u16,
        compute: impl FnOnce() -> EntryGeometry,
    ) -> EntryGeometry {
        let slot = self.width_slot(width);
        if self.heights.len() <= index {
            self.heights.resize_with(index + 1, EntryHeights::default);
        }
        let heights = &mut self.heights[index];
        if heights.rev != rev {
            heights.rev = rev;
            heights.by_width = [None, None];
        }
        if let Some(geometry) = heights.by_width[slot] {
            return geometry;
        }
        let geometry = compute();
        heights.by_width[slot] = Some(geometry);
        geometry
    }

    pub fn layout(&self, revision: u64, width: u16) -> Option<&TranscriptLayout> {
        let layout = &self.layouts[self.width_slot(width)];
        (layout.revision == revision).then_some(layout)
    }

    pub fn store_layout(
        &mut self,
        revision: u64,
        width: u16,
        height: u16,
        entries: Vec<LayoutEntry>,
        queue_top: Option<u16>,
    ) {
        let slot = self.width_slot(width);
        self.layouts[slot] = TranscriptLayout {
            revision,
            height,
            entries,
            queue_top,
        };
    }

    pub fn invalidate_layouts(&mut self) {
        for layout in &mut self.layouts {
            layout.revision = 0;
        }
    }

    fn width_slot(&self, width: u16) -> usize {
        debug_assert!(width == self.widths.0 || width == self.widths.1);
        usize::from(width != self.widths.0)
    }
}
