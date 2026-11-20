//! The `environment` object on `vibe.startup` keeps only short tokens, flags and sizes.

use serde_json::json;
use vibe_rs::telemetry::environment::{build, shell_name, split_version, token};
use vibe_rs::terminal_capabilities::{read, Capabilities};
use vibe_rs::terminal_detect::EnvSource;

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
fn a_tmux_session_over_ssh_reports_the_outer_terminal() {
    let env = FakeEnv(vec![
        ("TERM_PROGRAM", "tmux"),
        ("TERM", "tmux-256color"),
        ("COLORTERM", "truecolor"),
        ("TMUX", "/tmp/tmux-501/default,1,0"),
        ("SSH_CONNECTION", "10.0.0.1 5000 10.0.0.2 22"),
        ("SHELL", "/usr/bin/zsh"),
        ("LANG", "en_US.UTF-8"),
    ]);
    let skipped = Capabilities::default();
    assert_eq!(
        build(&env, &skipped, Some("iTerm2 3.5.0"), Some((120, 40)), false),
        json!({
            "term_program": "tmux",
            "term": "tmux-256color",
            "colorterm": "truecolor",
            "terminal_name": "iterm2",
            "terminal_version": "3.5.0",
            "terminal_columns": 120,
            "terminal_rows": 40,
            "multiplexer": "tmux",
            "is_ssh": true,
            "is_wsl": false,
            "is_container": false,
            "login_shell": "zsh",
            "is_no_color": false,
            "is_utf8": true,
            "probe_status": "skipped",
            "has_mouse_pixel_reports": null,
        })
    );
}

#[test]
fn an_empty_environment_reports_nothing_it_does_not_know() {
    let answered = read(b"\x1b[?62;22c");
    assert_eq!(
        build(&FakeEnv(vec![]), &answered, None, None, false),
        json!({
            "term_program": null,
            "term": null,
            "colorterm": null,
            "terminal_name": null,
            "terminal_version": null,
            "terminal_columns": null,
            "terminal_rows": null,
            "multiplexer": "none",
            "is_ssh": false,
            "is_wsl": false,
            "is_container": false,
            "login_shell": null,
            "is_no_color": false,
            "is_utf8": null,
            "probe_status": "answered",
            "has_mouse_pixel_reports": false,
        })
    );
}

#[test]
fn a_container_is_seen_from_its_marker_file_or_its_environment() {
    let skipped = Capabilities::default();
    let is_container =
        |env: &FakeEnv, marker| build(env, &skipped, None, None, marker)["is_container"].clone();
    assert_eq!(is_container(&FakeEnv(vec![]), true), true);
    assert_eq!(
        is_container(&FakeEnv(vec![("CODESPACES", "true")]), false),
        true
    );
    assert_eq!(
        is_container(&FakeEnv(vec![("container", "podman")]), false),
        true
    );
}

#[test]
fn xtversion_texts_split_into_name_and_version() {
    assert_eq!(
        split_version("kitty(0.35.2)"),
        (Some("kitty".into()), Some("0.35.2".into()))
    );
    assert_eq!(
        split_version("XTerm(390)"),
        (Some("xterm".into()), Some("390".into()))
    );
    assert_eq!(
        split_version("ghostty 1.1.3"),
        (Some("ghostty".into()), Some("1.1.3".into()))
    );
    assert_eq!(split_version("WezTerm"), (Some("wezterm".into()), None));
}

#[test]
fn shells_and_tokens_drop_paths_and_free_text() {
    assert_eq!(shell_name("/opt/homebrew/bin/fish"), Some("fish".into()));
    assert_eq!(
        shell_name(r"C:\Program Files\Git\usr\bin\bash.exe"),
        Some("bash".into())
    );
    assert_eq!(token("My Term/../x"), Some("myterm..x".into()));
    assert_eq!(token(&"a".repeat(100)).map(|token| token.len()), Some(40));
    assert_eq!(token("  "), None);
}
