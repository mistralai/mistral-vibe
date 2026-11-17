//! OS-specific queries, one implementation per platform.

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub use windows::{
    disable_vt_input, disable_window_input, enable_window_input, keep_key_records, on_altgr_layer,
    restore_vt_input,
};

/// Only the Windows console needs resize events requested; elsewhere nothing is added.
#[cfg(not(windows))]
pub fn enable_window_input() -> bool {
    false
}

#[cfg(not(windows))]
pub fn disable_window_input() {}

/// Only the Windows console has a VT input mode to turn off.
#[cfg(not(windows))]
pub fn disable_vt_input() -> bool {
    false
}

#[cfg(not(windows))]
pub fn restore_vt_input(_: bool) {}

#[cfg(not(windows))]
pub fn keep_key_records() {}

/// Whether the active keyboard layout types `c` with AltGr; only Windows reports AltGr as Ctrl+Alt.
#[cfg(not(windows))]
pub fn on_altgr_layer(_: char) -> bool {
    false
}
