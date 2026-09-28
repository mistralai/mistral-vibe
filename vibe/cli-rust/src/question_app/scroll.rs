//! Pure viewport reconciliation for pre-wrapped question rows.

use super::Viewport;

pub fn reconcile_scroll(
    viewport: Viewport,
    selected: usize,
    option_rows: &[(u16, usize)],
    total: u16,
    visible: u16,
) -> u16 {
    let mut offset = viewport.offset.min(total.saturating_sub(visible));
    if viewport.detached || visible == 0 {
        return offset;
    }
    let Some(first) = option_rows
        .iter()
        .find(|(_, index)| *index == selected)
        .map(|(row, _)| *row)
    else {
        return offset;
    };
    let last = option_rows
        .iter()
        .rfind(|(_, index)| *index == selected)
        .map_or(first, |(row, _)| *row);
    if first < offset {
        offset = first;
    } else if last >= offset.saturating_add(visible) {
        offset = last.saturating_add(1).saturating_sub(visible).min(first);
    }
    offset.min(total.saturating_sub(visible))
}

pub fn visible_option_rows(
    option_rows: &[(u16, usize)],
    box_y: u16,
    offset: u16,
    visible: u16,
) -> Vec<(u16, usize)> {
    let viewport_end = offset.saturating_add(visible);
    option_rows
        .iter()
        .filter_map(|&(row, index)| {
            if row < offset || row >= viewport_end {
                return None;
            }
            Some((box_y.saturating_add(1).saturating_add(row - offset), index))
        })
        .collect()
}
