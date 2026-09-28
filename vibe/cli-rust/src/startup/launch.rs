//! How the app-server is launched, and the startup timing marks.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};

use crate::server::{Launch, DEFAULT_APP_SERVER_ARGS};

/// Launch command from the process environment; see [`resolve_launch`] for the
/// precedence between replay, `VIBE_APP_SERVER_CMD`, `VIBE_APP_SERVER_BIN`, and
/// the source-checkout default.
pub fn launch_from_env(invocation_cwd: &Path) -> Launch {
    let configured_cwd = std::env::var_os("VIBE_APP_SERVER_CWD");
    let cwd = Some(resolve_launch_cwd(
        invocation_cwd,
        configured_cwd.as_deref(),
    ));
    resolve_launch(
        std::env::var("VIBE_APP_SERVER_CMD").ok(),
        std::env::var("VIBE_APP_SERVER_BIN").ok(),
        std::env::var("VIBE_REPLAY_BIN").ok(),
        std::env::var_os("VIBE_REPLAY_FIXTURE").is_some(),
        cwd,
    )
}

/// Decide how to launch the app-server from already-read env values. Precedence:
/// replay (`VIBE_REPLAY_BIN`) -> `VIBE_APP_SERVER_CMD` (a full command line, used
/// by the Makefile / source checkouts) -> `VIBE_APP_SERVER_BIN` (a single
/// installed binary path, used by wheel installs) -> the source-checkout default.
///
/// Split out as a pure function so it can be tested without touching process env.
pub fn resolve_launch(
    cmd: Option<String>,
    bin: Option<String>,
    replay: Option<String>,
    replaying: bool,
    cwd: Option<PathBuf>,
) -> Launch {
    if replaying {
        if let Some(replay) = replay {
            if !replay.trim().is_empty() {
                return Launch {
                    program: replay,
                    args: Vec::new(),
                    cwd,
                };
            }
        }
    }
    // Full command line (program + args), parsed with POSIX shell quoting so an
    // argument path with spaces survives (e.g. `uv run --with-editable "/a b/harness"
    // vibe-app-server --experimental-harness`). The value already carries every param
    // it needs, so the always-on params are not appended here. On unbalanced quotes
    // shlex returns None; fall back to a plain whitespace split rather than fail.
    if let Some(cmd) = cmd {
        if !cmd.trim().is_empty() {
            let tokens = shlex::split(&cmd)
                .unwrap_or_else(|| cmd.split_whitespace().map(str::to_owned).collect());
            let mut parts = tokens.into_iter();
            let program = parts.next().unwrap_or_else(|| "uv".into());
            return Launch {
                program,
                args: parts.collect(),
                cwd,
            };
        }
    }
    // Wheel install: the value is the installed app-server path, trimmed of
    // surrounding whitespace (interior spaces preserved, unlike CMD which is
    // split). Only the always-on params follow it.
    if let Some(bin) = bin {
        if !bin.trim().is_empty() {
            return Launch {
                program: bin.trim().to_owned(),
                args: DEFAULT_APP_SERVER_ARGS
                    .iter()
                    .map(|s| (*s).to_owned())
                    .collect(),
                cwd,
            };
        }
    }
    Launch {
        cwd,
        ..Launch::default()
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
