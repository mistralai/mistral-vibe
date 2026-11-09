//! Headless startup guards: keyring preflight notice and startup deadline.
use std::time::Duration;

use vibe_rs::headless_startup::{keyring_preflight_notice, within_startup_deadline};

#[test]
fn preflight_notice_only_when_the_key_comes_from_the_keyring() {
    let notice = keyring_preflight_notice(false, false).expect("keyring read is announced");
    assert!(notice.contains("Reading API keys"), "{notice}");
    assert!(!notice.contains("MISTRAL_API_KEY"), "{notice}");
    assert_eq!(keyring_preflight_notice(true, false), None);
    assert_eq!(keyring_preflight_notice(false, true), None);
}

#[tokio::test]
async fn startup_that_never_answers_fails_with_a_keyring_hint() {
    let stuck = std::future::pending::<anyhow::Result<()>>();

    let error = within_startup_deadline(Duration::from_millis(10), stuck)
        .await
        .expect_err("a stuck startup must fail");

    let message = error.to_string();
    assert!(message.contains("did not start"), "{message}");
    assert!(
        message.contains("provider's API key environment variable"),
        "{message}"
    );
    assert!(!message.contains("MISTRAL_API_KEY"), "{message}");
}
