//! Hit-map rows a painted entry adds: its groups' header rows and its chrome columns.

use std::ops::Range;

use super::entry;
use crate::selection::RowChrome;

/// Register the header row a group `key` paints after its gap row at `y`, returning where its content starts.
pub(super) fn push_header(
    hitmap: &mut Vec<(u16, u16, Option<u16>, String)>,
    y: i32,
    bounds: &Range<i32>,
    key: Option<&String>,
) -> i32 {
    let Some(key) = key else {
        return y;
    };
    let (top, bottom) = ((y + 1).max(bounds.start), (y + 2).min(bounds.end));
    if bottom > top {
        hitmap.push((top as u16, bottom as u16, None, key.clone()));
    }
    y + 2
}

/// Register the chrome of rows `(rail, content, bottom)`: a `Worked for` rail, then the entry's own columns.
pub(in crate::ui::transcript) fn push_chrome(
    chrome: &mut Vec<RowChrome>,
    (rail, content, bottom): (i32, i32, i32),
    railed: bool,
    (border, gutter): (u16, u16),
) {
    let indent = if railed { entry::DETAILS_INDENT } else { 0 };
    if railed && rail < content {
        chrome.push((rail as u16, content as u16, indent, 0, 0));
    }
    if indent > 0 || border > 0 || gutter > 0 {
        chrome.push((content as u16, bottom as u16, indent, border, gutter));
    }
}
