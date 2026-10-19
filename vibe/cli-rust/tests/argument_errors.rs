//! Exit codes of argument parsing: a bad argument is a usage error.

use std::process::Command;

fn run(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_vibe-rs"))
        .args(args)
        .env_remove("VIBE_CWD")
        .output()
        .unwrap()
}

#[test]
fn a_bad_argument_exits_one_with_the_parse_error() {
    for args in [
        &["--max-turns", "many", "-p", "hi"][..],
        &["--no-such-flag"][..],
        &["-c", "--resume", "abc"][..],
    ] {
        let output = run(args);
        assert_eq!(output.status.code(), Some(1), "{args:?}");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains("error:"), "{args:?}: {stderr}");
    }
}

#[test]
fn help_and_version_exit_zero() {
    for args in [&["--help"][..], &["--version"][..]] {
        let output = run(args);
        assert_eq!(output.status.code(), Some(0), "{args:?}");
        assert!(!output.stdout.is_empty(), "{args:?}");
    }
}
