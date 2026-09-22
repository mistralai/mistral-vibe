//! `-v`/`--version` flag parity with the Python `argparse` `action="version"`.

use clap::error::ErrorKind;
use clap::Parser;
use vibe_rs::cli::Cli;

#[test]
fn long_version_flag_is_recognized() {
    let err = Cli::try_parse_from(["vibe-rs", "--version"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::DisplayVersion);
}

#[test]
fn short_version_flag_is_recognized() {
    let err = Cli::try_parse_from(["vibe-rs", "-v"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::DisplayVersion);
}

#[test]
fn version_output_matches_python_format() {
    let err = Cli::try_parse_from(["vibe-rs", "--version"]).unwrap_err();
    let rendered = err.to_string();
    assert_eq!(rendered, format!("vibe {}\n", env!("CARGO_PKG_VERSION")));
}

#[test]
fn version_flag_does_not_conflict_with_resume() {
    let err = Cli::try_parse_from(["vibe-rs", "--version", "--resume", "abc"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::DisplayVersion);
}

#[test]
fn version_flag_does_not_conflict_with_continue() {
    let err = Cli::try_parse_from(["vibe-rs", "--version", "--continue"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::DisplayVersion);
}

#[test]
fn version_flag_short_circuits_before_unknown_args() {
    let err = Cli::try_parse_from(["vibe-rs", "--version", "--bogus"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::DisplayVersion);
}
