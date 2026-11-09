//! List scroll convention: the viewport follows the highlighted option with the
//! least movement, bringing the option's surrounding non-selectable lines along.

use std::ops::Range;

/// The top line that keeps `highlight` (the highlighted option's lines) in a
/// `visible`-line viewport over `total` lines, moving `offset` as little as possible.
///
/// The non-selectable lines directly above the option (its section header, or
/// the list's leading lines for the first option) and, for the last option, the
/// trailing lines come into view with it, so wrapping from the bottom back to
/// the top always shows the very top of the list. The option itself wins when
/// they do not all fit.
pub fn follow(
    offset: usize,
    visible: usize,
    total: usize,
    highlight: Range<usize>,
    is_option: impl Fn(usize) -> bool,
) -> usize {
    let max = total.saturating_sub(visible);
    let highlight = highlight.start..highlight.end.min(total);
    if visible == 0 || highlight.is_empty() {
        return offset.min(max);
    }
    let mut top = highlight.start;
    while top > 0 && !is_option(top - 1) {
        top -= 1;
    }
    let bottom = match (highlight.end..total).any(&is_option) {
        true => highlight.end,
        false => total,
    };
    let mut offset = offset.min(max);
    if top < offset {
        offset = top;
    } else if bottom > offset + visible {
        offset = bottom - visible;
    }
    offset
        .max(highlight.end.saturating_sub(visible))
        .min(highlight.start)
        .min(max)
}
