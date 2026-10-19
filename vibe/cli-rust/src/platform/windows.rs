//! Win32 queries.

use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetKeyboardLayout, VkKeyScanExW};
use windows_sys::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

/// Whether the foreground keyboard layout types `c` with Ctrl+Alt (AltGr), as crossterm resolves it.
pub fn on_altgr_layer(c: char) -> bool {
    let Ok(unit) = u16::try_from(u32::from(c)) else {
        return false;
    };
    // SAFETY: plain Win32 queries; the process-id out-parameter is optional and passed as null.
    let scan = unsafe {
        let thread = GetWindowThreadProcessId(GetForegroundWindow(), std::ptr::null_mut());
        // Conhost hides the foreground layout; fall back to this thread's, like `ToUnicodeEx` does.
        let layout = match GetKeyboardLayout(thread) {
            layout if layout.is_null() => GetKeyboardLayout(0),
            layout => layout,
        };
        VkKeyScanExW(unit, layout)
    };
    // The high byte holds the shift state (1 Shift, 2 Ctrl, 4 Alt); -1 means no key types `c`.
    scan != -1 && (scan >> 8) & 0b110 == 0b110
}
