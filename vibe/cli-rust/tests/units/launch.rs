//! `resolve_launch` precedence: replay -> CMD (full command) -> BIN (single
//! binary) -> source-checkout default.

use std::path::PathBuf;

use vibe_rs::server::DEFAULT_APP_SERVER_ARGS;
use vibe_rs::startup::resolve_launch;

fn cwd() -> Option<PathBuf> {
    Some(PathBuf::from("/work"))
}

#[test]
fn cmd_is_split_into_program_and_args_without_default_args() {
    let launch = resolve_launch(
        Some("uv run --with-editable ../harness vibe-app-server --experimental-harness".into()),
        None,
        None,
        false,
        cwd(),
    );
    assert_eq!(launch.program, "uv");
    assert_eq!(
        launch.args,
        vec![
            "run",
            "--with-editable",
            "../harness",
            "vibe-app-server",
            "--experimental-harness",
        ]
    );
    assert_eq!(launch.cwd, cwd());
}

#[test]
fn bin_is_trimmed_with_default_args() {
    let launch = resolve_launch(
        None,
        Some("  /venv/bin/vibe-app-server  ".into()),
        None,
        false,
        cwd(),
    );
    assert_eq!(launch.program, "/venv/bin/vibe-app-server");
    assert_eq!(launch.args, DEFAULT_APP_SERVER_ARGS);
}

#[test]
fn cmd_preserves_quoted_paths_with_spaces() {
    let launch = resolve_launch(
        Some(
            r#"uv run --with-editable "/a b/harness" vibe-app-server --experimental-harness"#
                .into(),
        ),
        None,
        None,
        false,
        cwd(),
    );
    assert_eq!(launch.program, "uv");
    assert_eq!(
        launch.args,
        vec![
            "run",
            "--with-editable",
            "/a b/harness",
            "vibe-app-server",
            "--experimental-harness",
        ]
    );
}

#[test]
fn cmd_unbalanced_quotes_fall_back_to_whitespace_split() {
    // shlex returns None on an unterminated quote; the launcher must degrade to
    // a whitespace split instead of panicking or dropping the command.
    let launch = resolve_launch(
        Some(r#"uv run "unterminated"#.into()),
        None,
        None,
        false,
        cwd(),
    );
    assert_eq!(launch.program, "uv");
    assert_eq!(launch.args, vec!["run", "\"unterminated"]);
}

#[test]
fn cmd_wins_over_bin() {
    let launch = resolve_launch(
        Some("uv run vibe-app-server --experimental-harness".into()),
        Some("/venv/bin/vibe-app-server".into()),
        None,
        false,
        cwd(),
    );
    assert_eq!(launch.program, "uv");
    assert_eq!(launch.args.first().map(String::as_str), Some("run"));
}

#[test]
fn blank_cmd_and_bin_fall_back_to_default() {
    let launch = resolve_launch(Some("   ".into()), Some("".into()), None, false, cwd());
    assert_eq!(launch.program, "uv");
    assert_eq!(
        launch.args,
        vec![
            "run",
            "--quiet",
            "vibe-app-server",
            "--experimental-harness"
        ]
    );
    assert_eq!(launch.cwd, cwd());
}

#[test]
fn replay_bin_wins_when_replaying() {
    let launch = resolve_launch(
        Some("uv run vibe-app-server".into()),
        Some("/venv/bin/vibe-app-server".into()),
        Some("/tmp/replay-bin".into()),
        true,
        cwd(),
    );
    assert_eq!(launch.program, "/tmp/replay-bin");
    assert!(launch.args.is_empty());
}

#[test]
fn replay_bin_ignored_when_not_replaying() {
    let launch = resolve_launch(None, None, Some("/tmp/replay-bin".into()), false, cwd());
    assert_eq!(launch.program, "uv");
}
