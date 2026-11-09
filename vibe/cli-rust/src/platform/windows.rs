//! Win32 queries.

use windows_sys::Win32::Foundation::{
    CloseHandle, GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
};
use windows_sys::Win32::System::Console::{GetConsoleMode, SetConsoleMode, ENABLE_WINDOW_INPUT};
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

/// Run `f` on `CONIN$`, the console input crossterm reads even when stdin is piped.
fn with_console_input<T>(f: impl FnOnce(HANDLE) -> Option<T>) -> Option<T> {
    let name: Vec<u16> = "CONIN$\0".encode_utf16().collect();
    // SAFETY: a NUL-terminated name, no security attributes and no template handle.
    let handle = unsafe {
        CreateFileW(
            name.as_ptr(),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            std::ptr::null(),
            OPEN_EXISTING,
            0,
            std::ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return None;
    }
    let result = f(handle);
    // SAFETY: `handle` was opened above and is not used afterwards.
    unsafe { CloseHandle(handle) };
    result
}

/// Keep console resize events, which mouse capture otherwise requests; true when this added the flag.
pub fn enable_window_input() -> bool {
    with_console_input(|handle| {
        let mut mode = 0;
        // SAFETY: `handle` is a console input handle and `mode` a valid out-parameter.
        let added = unsafe {
            GetConsoleMode(handle, &mut mode) != 0
                && mode & ENABLE_WINDOW_INPUT == 0
                && SetConsoleMode(handle, mode | ENABLE_WINDOW_INPUT) != 0
        };
        added.then_some(())
    })
    .is_some()
}

/// Clear the flag `enable_window_input` added, keeping the rest of the current console mode.
pub fn disable_window_input() {
    with_console_input(|handle| {
        let mut mode = 0;
        // SAFETY: `handle` is a console input handle and `mode` a valid out-parameter.
        let cleared = unsafe {
            GetConsoleMode(handle, &mut mode) != 0
                && SetConsoleMode(handle, mode & !ENABLE_WINDOW_INPUT) != 0
        };
        cleared.then_some(())
    });
}
