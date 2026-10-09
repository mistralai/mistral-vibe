//! List cursor moves: ↑↓ and j/k wrap around the ends; PageUp/PageDown and Home/End clamp;
//! a digit picks the numbered row it names.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

/// The row after `current` (or before it) in a list of `len`, wrapping at both ends.
pub fn wrap(current: usize, len: usize, down: bool) -> usize {
    wrap_selectable(current, len, down, |_| true).unwrap_or(0)
}

/// The next selectable row after `current` (or before it), wrapping; `None` when no row is selectable.
pub fn wrap_selectable(
    current: usize,
    len: usize,
    down: bool,
    selectable: impl Fn(usize) -> bool,
) -> Option<usize> {
    let start = current.min(len.checked_sub(1)?);
    (1..=len)
        .map(|step| match down {
            true => (start + step) % len,
            false => (start + len - step % len) % len,
        })
        .find(|&index| selectable(index))
}

/// The row a plain digit key names in a numbered list of `count` rows (`1` is row 0).
pub fn digit(key: &KeyEvent, count: usize) -> Option<usize> {
    if !key.modifiers.difference(KeyModifiers::SHIFT).is_empty() {
        return None;
    }
    let KeyCode::Char(ch) = key.code else {
        return None;
    };
    let index = (ch.to_digit(10)? as usize).checked_sub(1)?;
    (index < count).then_some(index)
}
