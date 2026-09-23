//! `--continue`/`--resume` CLI intent resolution, mirroring the Python
//! `_session_intent` (entrypoint.py).

use clap::Parser;
use vibe_rs::cli::{Cli, StartupResume};

#[test]
fn no_flags_is_no_intent() {
    let cli = Cli::parse_from(["vibe-rs"]);
    assert_eq!(cli.startup_resume(), StartupResume::None);
}

#[test]
fn continue_flag_is_continue_intent() {
    let cli = Cli::parse_from(["vibe-rs", "--continue"]);
    assert_eq!(cli.startup_resume(), StartupResume::Continue);
}

#[test]
fn short_continue_flag_is_continue_intent() {
    let cli = Cli::parse_from(["vibe-rs", "-c"]);
    assert_eq!(cli.startup_resume(), StartupResume::Continue);
}

#[test]
fn resume_with_id_is_by_id_intent() {
    let cli = Cli::parse_from(["vibe-rs", "--resume", "abc123"]);
    assert_eq!(cli.startup_resume(), StartupResume::ById("abc123".into()));
}

#[test]
fn resume_without_id_is_picker_intent() {
    let cli = Cli::parse_from(["vibe-rs", "--resume"]);
    assert_eq!(cli.startup_resume(), StartupResume::Picker);
}

#[test]
fn continue_and_resume_are_mutually_exclusive() {
    let result = Cli::try_parse_from(["vibe-rs", "--continue", "--resume", "id"]);
    assert!(result.is_err(), "--continue and --resume must conflict");
}

#[test]
fn resume_id_and_resume_picker_are_distinct() {
    assert_ne!(
        Cli::parse_from(["vibe-rs", "--resume", "id"]).startup_resume(),
        Cli::parse_from(["vibe-rs", "--resume"]).startup_resume(),
    );
}

#[test]
fn no_intent_keeps_the_started_session() {
    assert_eq!(StartupResume::None.target(None), None);
}

/// The picker attaches the fresh session; Esc has to land somewhere.
#[test]
fn the_picker_keeps_the_started_session() {
    assert_eq!(StartupResume::Picker.target(Some("latest".into())), None);
}

#[test]
fn resume_by_id_attaches_that_session() {
    let id = StartupResume::ById("abc".into());
    assert_eq!(id.target(Some("latest".into())), Some("abc".into()));
}

#[test]
fn continue_attaches_the_session_the_list_resolved() {
    let resolved = StartupResume::Continue.target(Some("latest".into()));
    assert_eq!(resolved, Some("latest".into()));
    assert_eq!(StartupResume::Continue.target(None), None);
}
