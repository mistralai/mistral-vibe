//! Terminal-emulator detection mapping (Python `cli/terminal_detect.py`).

use vibe_rs::terminal_detect::{
    detect_from, from_env_markers, from_term_program, is_vscode_family, EnvSource,
};

struct FakeEnv(Vec<(&'static str, &'static str)>);

impl EnvSource for FakeEnv {
    fn get(&self, name: &str) -> Option<String> {
        self.0
            .iter()
            .find(|(key, _)| *key == name)
            .map(|(_, value)| (*value).to_owned())
    }
}

#[test]
fn term_program_map_matches_python() {
    let cases = [
        ("apple_terminal", Some("apple_terminal")),
        ("iterm.app", Some("iterm2")),
        ("wezterm", Some("wezterm")),
        ("ghostty", Some("ghostty")),
        ("alacritty", Some("alacritty")),
        ("kitty", Some("kitty")),
        ("hyper", Some("hyper")),
        ("vscode", None),
        ("", None),
        ("screen", None),
    ];
    for (input, expected) in cases {
        assert_eq!(from_term_program(input), expected, "TERM_PROGRAM={input}");
    }
}

#[test]
fn env_markers_match_python() {
    let markers = [
        ("WEZTERM_PANE", "wezterm"),
        ("GHOSTTY_RESOURCES_DIR", "ghostty"),
        ("KITTY_WINDOW_ID", "kitty"),
        ("ALACRITTY_SOCKET", "alacritty"),
        ("ALACRITTY_LOG", "alacritty"),
        ("WT_SESSION", "windows_terminal"),
        ("WT_PROFILE_ID", "windows_terminal"),
    ];
    for (name, terminal) in markers {
        let env = FakeEnv(vec![(name, "1")]);
        assert_eq!(from_env_markers(&env), Some(terminal), "{name}");
    }
    assert_eq!(from_env_markers(&FakeEnv(vec![])), None);
    assert_eq!(
        from_env_markers(&FakeEnv(vec![("TERMINAL_EMULATOR", "JetBrains-Monospace")])),
        Some("jetbrains")
    );
}

#[test]
fn empty_marker_value_is_not_a_match() {
    let env = FakeEnv(vec![("WEZTERM_PANE", "")]);
    assert_eq!(from_env_markers(&env), None);
}

#[test]
fn vscode_hosts_resolve_cursor_insiders_and_stable() {
    let cursor = FakeEnv(vec![
        ("TERM_PROGRAM", "vscode"),
        (
            "VSCODE_GIT_ASKPASS_NODE",
            "/Applications/Cursor.app/Contents/Frameworks/node",
        ),
    ]);
    assert_eq!(detect_from(&cursor), "cursor");
    let insiders = FakeEnv(vec![
        ("TERM_PROGRAM", "vscode"),
        ("TERM_PROGRAM_VERSION", "1.90.0-insider"),
    ]);
    assert_eq!(detect_from(&insiders), "vscode_insiders");
    assert_eq!(
        detect_from(&FakeEnv(vec![("TERM_PROGRAM", "vscode")])),
        "vscode"
    );
}

#[test]
fn vscode_family_matches_python() {
    for terminal in ["vscode", "vscode_insiders", "cursor"] {
        assert!(is_vscode_family(terminal), "{terminal}");
    }
    for terminal in ["jetbrains", "ghostty", "windows_terminal", "unknown"] {
        assert!(!is_vscode_family(terminal), "{terminal}");
    }
}

#[test]
fn unknown_term_program_falls_back_to_env_markers() {
    let env = FakeEnv(vec![("TERM_PROGRAM", "tmux"), ("KITTY_WINDOW_ID", "1")]);
    assert_eq!(detect_from(&env), "kitty");
    assert_eq!(detect_from(&FakeEnv(vec![])), "unknown");
}
