//! Terminal-emulator detection (Python `cli/terminal_detect.py`).

/// Python `TerminalEmulator` values, sent as `ClientInfo.terminalEmulator`.
pub const VSCODE: &str = "vscode";
pub const VSCODE_INSIDERS: &str = "vscode_insiders";
pub const CURSOR: &str = "cursor";
pub const JETBRAINS: &str = "jetbrains";
pub const APPLE_TERMINAL: &str = "apple_terminal";
pub const ITERM2: &str = "iterm2";
pub const WEZTERM: &str = "wezterm";
pub const GHOSTTY: &str = "ghostty";
pub const ALACRITTY: &str = "alacritty";
pub const KITTY: &str = "kitty";
pub const HYPER: &str = "hyper";
pub const WINDOWS_TERMINAL: &str = "windows_terminal";
pub const UNKNOWN: &str = "unknown";

/// Detect the host terminal from the process environment (Python `detect_terminal`).
pub fn detect() -> &'static str {
    detect_from(&Env)
}

/// `detect` over any environment source.
pub fn detect_from(env: &dyn EnvSource) -> &'static str {
    let term_program = env.get("TERM_PROGRAM").unwrap_or_default().to_lowercase();
    if term_program == "vscode" {
        if is_cursor(env) {
            return CURSOR;
        }
        let version = env.get("TERM_PROGRAM_VERSION").unwrap_or_default();
        return if version.to_lowercase().ends_with("-insider") {
            VSCODE_INSIDERS
        } else {
            VSCODE
        };
    }
    if let Some(terminal) = from_term_program(&term_program) {
        return terminal;
    }
    from_env_markers(env).unwrap_or(UNKNOWN)
}

/// Python `term_map`: `TERM_PROGRAM` values other than `vscode`.
pub fn from_term_program(term_program: &str) -> Option<&'static str> {
    match term_program {
        "apple_terminal" => Some(APPLE_TERMINAL),
        "iterm.app" => Some(ITERM2),
        "wezterm" => Some(WEZTERM),
        "ghostty" => Some(GHOSTTY),
        "alacritty" => Some(ALACRITTY),
        "kitty" => Some(KITTY),
        "hyper" => Some(HYPER),
        _ => None,
    }
}

/// Python `_detect_terminal_from_env`: multiplexer and shell markers.
pub fn from_env_markers(env: &dyn EnvSource) -> Option<&'static str> {
    let markers = [
        ("WEZTERM_PANE", WEZTERM),
        ("GHOSTTY_RESOURCES_DIR", GHOSTTY),
        ("KITTY_WINDOW_ID", KITTY),
        ("ALACRITTY_SOCKET", ALACRITTY),
        ("ALACRITTY_LOG", ALACRITTY),
        ("WT_SESSION", WINDOWS_TERMINAL),
        ("WT_PROFILE_ID", WINDOWS_TERMINAL),
    ];
    for (name, terminal) in markers {
        if env.present(name) {
            return Some(terminal);
        }
    }
    if env
        .get("TERMINAL_EMULATOR")
        .is_some_and(|value| value.to_lowercase().contains("jetbrains"))
    {
        return Some(JETBRAINS);
    }
    None
}

/// Python `_is_cursor`: VS Code-env paths mention Cursor when it hosts the terminal.
fn is_cursor(env: &dyn EnvSource) -> bool {
    [
        "VSCODE_GIT_ASKPASS_NODE",
        "VSCODE_GIT_ASKPASS_MAIN",
        "VSCODE_IPC_HOOK_CLI",
        "VSCODE_NLS_CONFIG",
    ]
    .iter()
    .any(|name| {
        env.get(name)
            .unwrap_or_default()
            .to_lowercase()
            .contains("cursor")
    })
}

pub trait EnvSource {
    fn get(&self, name: &str) -> Option<String>;
    fn present(&self, name: &str) -> bool {
        self.get(name).is_some_and(|value| !value.is_empty())
    }
}

struct Env;

impl EnvSource for Env {
    fn get(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }
}
