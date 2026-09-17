//! How the app-server is launched, and the startup timing marks.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};

use crate::server::{Launch, DEFAULT_APP_SERVER_ARGS};

/// Launch command; in replay mode `VIBE_REPLAY_BIN`, else the installed
/// app-server via `VIBE_APP_SERVER_BIN`, else the source-checkout default.
pub fn launch_from_env(invocation_cwd: &Path) -> Launch {
    let configured_cwd = std::env::var_os("VIBE_APP_SERVER_CWD");
    let cwd = Some(resolve_launch_cwd(
        invocation_cwd,
        configured_cwd.as_deref(),
    ));
    if std::env::var_os("VIBE_REPLAY_FIXTURE").is_some() {
        if let Ok(bin) = std::env::var("VIBE_REPLAY_BIN") {
            if !bin.trim().is_empty() {
                return Launch {
                    program: bin,
                    args: Vec::new(),
                    cwd,
                };
            }
        }
    }
    match std::env::var("VIBE_APP_SERVER_BIN") {
        // Wheel install: the value is the installed app-server path, taken
        // verbatim (spaces and all). Only the always-on params follow it.
        Ok(bin) if !bin.trim().is_empty() => Launch {
            program: bin.trim().to_owned(),
            args: DEFAULT_APP_SERVER_ARGS
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
            cwd,
        },
        _ => Launch {
            cwd,
            ..Launch::default()
        },
    }
}

/// Anchor the app-server cwd before `--workdir` changes the process directory.
pub fn resolve_launch_cwd(invocation_cwd: &Path, configured: Option<&OsStr>) -> PathBuf {
    let Some(configured) = configured.filter(|path| !path.is_empty()) else {
        return invocation_cwd.to_path_buf();
    };
    let configured = PathBuf::from(configured);
    if configured.is_absolute() {
        configured
    } else {
        invocation_cwd.join(configured)
    }
}

use std::time::Instant;

pub struct StartupRecorder {
    enabled: bool,
    start: Instant,
    marks: Vec<(&'static str, Instant)>,
}

impl Default for StartupRecorder {
    fn default() -> Self {
        Self::new()
    }
}

impl StartupRecorder {
    pub fn new() -> Self {
        let enabled = std::env::var("VIBE_STARTUP_TIMINGS")
            .map(|v| !v.is_empty())
            .unwrap_or(false);
        Self {
            enabled,
            start: Instant::now(),
            marks: Vec::new(),
        }
    }

    pub fn record(&mut self, name: &'static str) {
        if self.enabled {
            self.marks.push((name, Instant::now()));
        }
    }

    pub fn flush(&self) {
        if !self.enabled {
            return;
        }
        for (name, t) in &self.marks {
            eprintln!(
                "vibe-startup {}={}ms",
                name,
                t.duration_since(self.start).as_millis()
            );
        }
    }
}
