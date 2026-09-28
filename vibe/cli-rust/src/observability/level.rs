//! Log-level precedence chain: session override, env, config, default.

use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Mutex;

pub const DEFAULT_LOG_LEVEL: &str = "WARNING";
pub const LOG_LEVELS: [&str; 5] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LogLevelChain {
    pub session: Option<String>,
    pub env: Option<String>,
    pub config: Option<String>,
    pub effective: String,
}

#[derive(Default)]
struct LogLevelState {
    session_override: Option<String>,
    config_level: Option<String>,
}

static STATE: Mutex<LogLevelState> = Mutex::new(LogLevelState {
    session_override: None,
    config_level: None,
});
static EFFECTIVE: AtomicU8 = AtomicU8::new(30);

/// Python's numeric levels, so ordering and CRITICAL-silences-everything match.
pub fn severity(level: &str) -> u8 {
    match level {
        "DEBUG" => 10,
        "INFO" => 20,
        "ERROR" => 40,
        "CRITICAL" => 50,
        _ => 30,
    }
}

pub fn normalize(level: &str) -> Option<String> {
    let upper = level.trim().to_ascii_uppercase();
    LOG_LEVELS.contains(&upper.as_str()).then_some(upper)
}

fn env_log_level() -> Option<String> {
    if std::env::var("DEBUG_MODE").is_ok_and(|value| value == "true") {
        return Some("DEBUG".to_owned());
    }
    std::env::var("LOG_LEVEL")
        .ok()
        .and_then(|value| normalize(&value))
}

pub fn get_log_level_chain() -> LogLevelChain {
    let state = STATE.lock().unwrap_or_else(|err| err.into_inner());
    let env = env_log_level();
    let effective = state
        .session_override
        .clone()
        .or_else(|| env.clone())
        .or_else(|| state.config_level.clone())
        .unwrap_or_else(|| DEFAULT_LOG_LEVEL.to_owned());
    LogLevelChain {
        session: state.session_override.clone(),
        env,
        config: state.config_level.clone(),
        effective,
    }
}

pub fn get_effective_log_level() -> String {
    get_log_level_chain().effective
}

pub fn get_session_override() -> Option<String> {
    get_log_level_chain().session
}

pub fn set_session_override(level: Option<&str>) {
    mutate(|state| state.session_override = level.and_then(normalize));
}

pub fn set_config_log_level(level: Option<&str>) {
    mutate(|state| state.config_level = level.and_then(normalize));
}

fn mutate(update: impl FnOnce(&mut LogLevelState)) {
    {
        let mut state = STATE.lock().unwrap_or_else(|err| err.into_inner());
        update(&mut state);
    }
    apply_effective();
}

pub fn apply_effective() {
    EFFECTIVE.store(severity(&get_effective_log_level()), Ordering::Relaxed);
}

/// Hot path for the tracing layer; TRACE folds into DEBUG, WARN into WARNING.
pub fn enabled(level: &tracing::Level) -> bool {
    let record = match *level {
        tracing::Level::TRACE | tracing::Level::DEBUG => 10,
        tracing::Level::INFO => 20,
        tracing::Level::WARN => 30,
        tracing::Level::ERROR => 40,
    };
    record >= EFFECTIVE.load(Ordering::Relaxed)
}
