//! Replay-harness seams: pinned bottom-bar values so client-e2e goldens are machine-independent.

use crate::utils::is_replaying;

const FOOTER_CWD: &str = "/test/workdir";

/// Pin the bottom-bar cwd under replay; the real value otherwise.
pub fn footer_cwd(cwd: String) -> String {
    if is_replaying() {
        FOOTER_CWD.to_string()
    } else {
        cwd
    }
}
