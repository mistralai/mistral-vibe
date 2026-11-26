//! The entrypoint `vibe-acp --setup` passes through `VIBE_SETUP_ENTRYPOINT`.
use vibe_rs::startup::is_acp_setup;

#[test]
fn setup_reports_the_acp_entrypoint() {
    assert!(is_acp_setup(true, Some("acp")));
}

#[test]
fn unset_env_keeps_the_default() {
    assert!(!is_acp_setup(true, None));
}

#[test]
fn env_is_ignored_outside_setup() {
    assert!(!is_acp_setup(false, Some("acp")));
}

#[test]
fn unknown_values_are_ignored() {
    assert!(!is_acp_setup(true, Some("vscode")));
}
