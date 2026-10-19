//! OS-specific queries, one implementation per platform.

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub use windows::on_altgr_layer;

/// Whether the active keyboard layout types `c` with AltGr; only Windows reports AltGr as Ctrl+Alt.
#[cfg(not(windows))]
pub fn on_altgr_layer(_: char) -> bool {
    false
}
