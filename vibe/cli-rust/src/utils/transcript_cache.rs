//! Cached transcript heights and cumulative positions for fast viewport lookup.

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
}

#[derive(Default)]
pub struct TranscriptCache {
    heights: Vec<EntryHeights>,
    layouts: [TranscriptLayout; 2],
    widths: (u16, u16),
    theme: usize,
    queue_paused: bool,
}

impl TranscriptCache {
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
    ) {
        let slot = self.width_slot(width);
        self.layouts[slot] = TranscriptLayout {
            revision,
            height,
            entries,
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
