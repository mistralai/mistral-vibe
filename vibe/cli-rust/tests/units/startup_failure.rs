//! Startup failures reach Sentry only as client bugs, grouped by category.

use vibe_rs::server::{RequestFailure, RpcError};
use vibe_rs::startup::failure::{startup_fingerprint, ServerFailure};

fn fingerprint(category: &str) -> Option<[String; 2]> {
    Some(["vibe-rs-startup".to_owned(), category.to_owned()])
}

#[test]
fn server_errors_are_left_to_the_server() {
    let refused = RpcError::synthetic("internal_error", "MCP server 'gh-alice' failed to start");
    let error = anyhow::Error::new(RequestFailure::Rpc(refused)).context("session/start failed");
    assert_eq!(startup_fingerprint(&error), None);
}

#[test]
fn a_worktree_the_server_could_not_prepare_is_not_reported() {
    let failure = ServerFailure("fatal: '/Users/bob/proj' is not a git repository".to_owned());
    assert_eq!(startup_fingerprint(&anyhow::Error::new(failure)), None);
}

#[test]
fn a_dead_app_server_groups_into_one_issue() {
    let error = anyhow::Error::new(RequestFailure::AppServerClosed).context("session/ready/wait");
    assert_eq!(
        startup_fingerprint(&error),
        fingerprint("app_server_closed")
    );
}

#[test]
fn a_client_bug_never_carries_its_message_into_the_fingerprint() {
    let error = anyhow::anyhow!("missing field `id`").context("session 42 state is malformed");
    assert_eq!(startup_fingerprint(&error), fingerprint("unclassified"));
}

#[test]
fn a_broken_environment_is_not_reported() {
    for kind in [
        std::io::ErrorKind::BrokenPipe,
        std::io::ErrorKind::WouldBlock,
    ] {
        let error = anyhow::Error::new(std::io::Error::from(kind)).context("spawn app-server");
        assert_eq!(startup_fingerprint(&error), None, "{kind:?}");
    }
}
