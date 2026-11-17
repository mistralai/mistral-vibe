//! Win32 queries.

use std::fs::{File, OpenOptions};
use std::os::windows::io::AsRawHandle;
use std::sync::{Mutex, MutexGuard, PoisonError};

use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::System::Console::{
    GetConsoleMode, SetConsoleMode, ENABLE_VIRTUAL_TERMINAL_INPUT, ENABLE_WINDOW_INPUT,
};
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

/// `CONIN$`, the console input crossterm reads even when stdin is piped.
fn console_input() -> Option<File> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .open("CONIN$")
        .ok()
}

/// Set or clear `flag` in the console input mode, keeping the rest; true when this changed it.
fn set_input_flag(handle: HANDLE, flag: u32, on: bool) -> bool {
    let mut mode = 0;
    // SAFETY: `handle` is a console input handle and `mode` a valid out-parameter.
    unsafe {
        GetConsoleMode(handle, &mut mode) != 0
            && (mode & flag != 0) != on
            && SetConsoleMode(handle, mode ^ flag) != 0
    }
}

fn set_console_input_flag(flag: u32, on: bool) -> bool {
    console_input().is_some_and(|input| set_input_flag(input.as_raw_handle(), flag, on))
}

/// Keep console resize events, which mouse capture otherwise requests; true when this added the flag.
pub fn enable_window_input() -> bool {
    set_console_input_flag(ENABLE_WINDOW_INPUT, true)
}

/// Clear the flag `enable_window_input` added, keeping the rest of the current console mode.
pub fn disable_window_input() {
    set_console_input_flag(ENABLE_WINDOW_INPUT, false);
}

/// Whether the TUI owns the console; held across each VT input change so the reader never undoes a restore.
static OWNED: Mutex<bool> = Mutex::new(false);

fn owned() -> MutexGuard<'static, bool> {
    OWNED.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Turn VT input off, as crossterm reads Win32 key records; true when it was on.
pub fn disable_vt_input() -> bool {
    let mut owned = owned();
    *owned = true;
    set_console_input_flag(ENABLE_VIRTUAL_TERMINAL_INPUT, false)
}

/// Stop keeping VT input off, turning it back on when `disable_vt_input` found it on.
pub fn restore_vt_input(was_on: bool) {
    let mut owned = owned();
    *owned = false;
    if was_on {
        set_console_input_flag(ENABLE_VIRTUAL_TERMINAL_INPUT, true);
    }
}

/// Turn VT input back off when another process on the console turned it on mid-session.
pub fn keep_key_records() {
    thread_local! {
        static INPUT: Option<File> = console_input();
    }
    let owned = owned();
    if !*owned {
        return;
    }
    INPUT.with(|input| {
        if let Some(input) = input {
            set_input_flag(input.as_raw_handle(), ENABLE_VIRTUAL_TERMINAL_INPUT, false);
        }
    });
}
