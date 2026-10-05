//! The `update` -> `--check-upgrade` argv rewrite (Python `parse_arguments`).

use std::ffi::OsString;

use vibe_rs::cli::rewrite_update_argv;

fn argv(args: &[&str]) -> Vec<OsString> {
    // The rewrite sees the same vector clap's parse_from does: program first.
    let mut full = vec![OsString::from("vibe-rs")];
    full.extend(args.iter().map(OsString::from));
    full
}

fn assert_rewrite(input: &[&str], rewritten: &[&str]) {
    let want = argv(rewritten);
    assert_eq!(
        rewrite_update_argv(argv(input)),
        want,
        "{input:?} must rewrite"
    );
}

#[test]
fn a_leading_bare_update_is_the_check_upgrade_command() {
    assert_rewrite(&["update"], &["--check-upgrade"]);
    assert_rewrite(
        &["update", "--workdir", "."],
        &["--check-upgrade", "--workdir", "."],
    );
}

#[test]
fn only_the_first_position_rewrites() {
    // `--` makes everything that follows a prompt; `--prompt` is its value.
    assert_rewrite(&["--", "update"], &["--", "update"]);
    assert_rewrite(&["--", "--check-upgrade"], &["--", "--check-upgrade"]);
    assert_rewrite(&["--prompt", "update"], &["--prompt", "update"]);
    assert_rewrite(&["--prompt=--check-upgrade"], &["--prompt=--check-upgrade"]);
    assert_rewrite(
        &["--check-upgrade", "--", "prompt"],
        &["--check-upgrade", "--", "prompt"],
    );
    assert_rewrite(
        &["--workdir", ".", "--check-upgrade"],
        &["--workdir", ".", "--check-upgrade"],
    );
    assert_rewrite(&["--check-upgrade"], &["--check-upgrade"]);
    assert_rewrite(&[], &[]);
    // `update` anywhere but first stays a positional prompt.
    assert_rewrite(&["prompt", "update"], &["prompt", "update"]);
    assert_rewrite(&["Update", "update"], &["Update", "update"]);
}
