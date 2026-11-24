//! Replay-harness seams: pinned bottom-bar values so client-e2e goldens are machine-independent.

use crate::utils::is_replaying;

const FOOTER_CWD: &str = "/test/workdir";

/// Pin the bottom-bar cwd under replay, keeping the part below the launch folder; the real value otherwise.
pub fn footer_cwd(cwd: String) -> String {
    if !is_replaying() {
        return cwd;
    }
    let launch = std::env::current_dir().unwrap_or_default();
    match std::path::Path::new(&cwd).strip_prefix(launch) {
        Ok(rest) if !rest.as_os_str().is_empty() => format!("{FOOTER_CWD}/{}", rest.display()),
        _ => FOOTER_CWD.to_string(),
    }
}
