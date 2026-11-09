//! OS-specific queries, one implementation per platform.

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub use windows::{disable_window_input, enable_window_input, on_altgr_layer};

/// Only the Windows console needs resize events requested; elsewhere nothing is added.
#[cfg(not(windows))]
pub fn enable_window_input() -> bool {
    false
}

#[cfg(not(windows))]
pub fn disable_window_input() {}

/// Whether the active keyboard layout types `c` with AltGr; only Windows reports AltGr as Ctrl+Alt.
#[cfg(not(windows))]
pub fn on_altgr_layer(_: char) -> bool {
    false
}
