//! The terminal identity `--continue` uses to reopen this terminal's last session.

/// Overrides the detected key; an empty value means "no terminal".
pub const OVERRIDE_ENV: &str = "VIBE_TERMINAL_KEY";

/// The key of the terminal this process runs in, if any.
pub fn current() -> Option<String> {
    resolve(std::env::var(OVERRIDE_ENV).ok(), detect)
}

/// The override when set, else the detected terminal.
pub fn resolve(
    override_value: Option<String>,
    detect: impl FnOnce() -> Option<String>,
) -> Option<String> {
    match override_value {
        Some(value) => (!value.is_empty()).then_some(value),
        None => detect(),
    }
}

/// The tty device of the first standard stream that is one.
#[cfg(unix)]
fn detect() -> Option<String> {
    let mut buffer = [0 as libc::c_char; 256];
    [0, 1, 2].into_iter().find_map(|fd| {
        // SAFETY: the buffer outlives the call, which receives its length.
        let status = unsafe { libc::ttyname_r(fd, buffer.as_mut_ptr(), buffer.len()) };
        // SAFETY: on success ttyname_r wrote a NUL-terminated name into the buffer.
        (status == 0)
            .then(|| unsafe { std::ffi::CStr::from_ptr(buffer.as_ptr()) })
            .map(|name| name.to_string_lossy().into_owned())
    })
}

/// The console window, else the Windows Terminal session, while a standard stream is a console.
#[cfg(windows)]
fn detect() -> Option<String> {
    use std::io::IsTerminal;
    let streams = [
        std::io::stdin().is_terminal(),
        std::io::stdout().is_terminal(),
        std::io::stderr().is_terminal(),
    ];
    if !streams.contains(&true) {
        return None;
    }
    // SAFETY: a plain Win32 query without arguments.
    let window = unsafe { windows_sys::Win32::System::Console::GetConsoleWindow() };
    if !window.is_null() {
        return Some(format!("console-{}", window as usize));
    }
    std::env::var("WT_SESSION")
        .ok()
        .filter(|session| !session.is_empty())
        .map(|session| format!("wt-{session}"))
}
