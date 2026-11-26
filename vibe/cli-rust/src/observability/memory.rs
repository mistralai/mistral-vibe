//! Exit-time peak RSS sampling for memory-leak triage.

/// This process's lifetime peak resident set size in kilobytes; `ru_maxrss`
/// is reported in bytes on macOS and kilobytes on Linux. Memory is never given
/// back to the OS, so the peak is a faithful stand-in for the exit value.
#[cfg(unix)]
pub fn peak_rss_kilobytes() -> Option<u64> {
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    let result = unsafe { libc::getrusage(libc::RUSAGE_SELF, &raw mut usage) };
    if result != 0 {
        return None;
    }
    let kilobytes = usage.ru_maxrss as u64;
    Some(if cfg!(target_os = "macos") {
        kilobytes / 1024
    } else {
        kilobytes
    })
}

#[cfg(unix)]
pub fn log_exit_rss() {
    if let Some(kilobytes) = peak_rss_kilobytes() {
        tracing::debug!(vibe_boundary = "lifecycle", peak_rss_kb = kilobytes, "exit");
    }
}

#[cfg(not(unix))]
pub fn log_exit_rss() {}
