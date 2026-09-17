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
