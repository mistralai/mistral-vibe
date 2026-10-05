//! Pure scroll arithmetic: clamp the scroll-up offset and resolve the viewport top.

/// Resolved view: clamped `scroll`, the top line to render from, and whether content overflows.
pub struct ScrollView {
    pub scroll: u16,
    pub position: u16,
    pub overflow: bool,
}

/// Fraction of the remaining distance covered per animation frame (ease-out).
const SCROLL_EASE: f32 = 0.3;

/// Advance `current` one animation step toward `target`, easing out with a
/// one-line floor so the glide always finishes. Returns the next scroll offset.
pub fn ease_scroll(current: u16, target: u16) -> u16 {
    if current == target {
        return target;
    }
    let dist = current.abs_diff(target);
    let step = ((dist as f32 * SCROLL_EASE).round() as u16).clamp(1, dist);
    if current < target {
        current + step
    } else {
        current - step
    }
}

/// While scrolled up, absorb the document's height change into the scroll-up
/// offset so the viewport keeps showing the same lines as content streams in.
pub fn absorb_growth(scroll: &mut u16, target: &mut u16, total: u16, last_total: u16) {
    if *target == 0 {
        return;
    }
    let delta = (total as i32 - last_total as i32).clamp(i16::MIN as i32, i16::MAX as i32) as i16;
    *target = target.saturating_add_signed(delta);
    *scroll = scroll.saturating_add_signed(delta);
}

/// A toggled entry held steady through the relayout its toggle causes.
#[derive(Clone, Debug, PartialEq)]
pub struct ScrollAnchor {
    /// Transcript index of the entry whose top stays put.
    pub index: usize,
    /// Its top row relative to the viewport top in the last frame; negative when cut off.
    pub row: i32,
    /// Its height in the last frame.
    pub height: u16,
    /// Viewport row its header lands on when the header had scrolled out above.
    pub landing: u16,
    /// The clicked toggle key; `None` for the Ctrl+O bulk toggle.
    pub key: Option<String>,
    /// Whether the toggle expanded `key`, so its block scrolls into view.
    pub reveal: bool,
}

/// Ctrl+O anchor: the entry at the viewport top, or none while pinned to the newest content.
pub fn bulk_toggle_anchor(target: u16, rows: &[(usize, i32, u16)]) -> Option<ScrollAnchor> {
    let &(index, row, height) = rows.first().filter(|_| target != 0)?;
    Some(ScrollAnchor {
        index,
        row,
        height,
        landing: 0,
        key: None,
        reveal: false,
    })
}

/// Viewport row for the anchored entry's top once `height` tall; its header sits `header` rows below.
pub fn anchored_row(anchor: &ScrollAnchor, header: u16, height: u16) -> i32 {
    let header_hidden = anchor.row + i32::from(header) < 0;
    if header_hidden && height != anchor.height {
        return i32::from(anchor.landing) - i32::from(header);
    }
    anchor.row
}

/// Advance viewport top line `position` just enough to show rows `[top, bottom)`, keeping `top` in view.
pub fn reveal(position: i32, viewport: u16, top: u16, bottom: u16) -> i32 {
    let overflow = i32::from(bottom) - (position + i32::from(viewport));
    position + overflow.min(i32::from(top) - position).max(0)
}

/// Scroll-up offset that puts document line `position` at the viewport top, clamped.
pub fn offset_at(total: u16, viewport: u16, position: i32) -> u16 {
    let max_scroll = total.saturating_sub(viewport);
    max_scroll - position.clamp(0, i32::from(max_scroll)) as u16
}

/// Given total content height, viewport height, and the requested scroll-up
/// offset (lines lifted off the bottom), clamp it and resolve the top line.
pub fn scroll_view(total: u16, viewport: u16, scroll: u16) -> ScrollView {
    if total <= viewport {
        return ScrollView {
            scroll: 0,
            position: 0,
            overflow: false,
        };
    }
    let max_scroll = total - viewport;
    let scroll = scroll.min(max_scroll);
    ScrollView {
        scroll,
        position: max_scroll - scroll,
        overflow: true,
    }
}
