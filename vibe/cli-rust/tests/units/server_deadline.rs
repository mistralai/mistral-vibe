//! The shared request deadline: an answer in time passes through, a late one fails naming its method.

use std::time::Duration;

use vibe_rs::server::deadline::within;

#[tokio::test]
async fn an_answer_that_never_comes_fails_once_its_time_is_up() {
    let never = std::future::pending::<u8>();
    let error = within("skills/detail", Duration::from_millis(5), never)
        .await
        .unwrap_err();
    assert_eq!(error.to_string(), "skills/detail timed out");
}

#[tokio::test]
async fn an_answer_in_time_passes_through() {
    let answer = within("skills/detail", Duration::from_secs(5), async { 7 }).await;
    assert_eq!(answer.unwrap(), 7);
}
