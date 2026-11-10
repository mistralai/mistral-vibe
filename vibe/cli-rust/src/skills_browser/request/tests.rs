use std::time::Duration;

use super::send;
use crate::server::{Client, SkillChange};

#[tokio::test]
async fn a_change_that_never_answers_fails_once_its_time_is_up() {
    let (client, _frames) = Client::stub_silent();
    let change = SkillChange::SetEnabled {
        name: "lint".to_owned(),
        enabled: false,
    };
    let outcome = send(&client, "s", &[change], Duration::from_millis(10)).await;
    assert_eq!(
        outcome.error.as_deref(),
        Some("skills/setEnabled timed out")
    );
    assert!(outcome.answer.is_none());
}

#[tokio::test]
async fn a_change_that_runs_out_of_time_stops_the_changes_after_it() {
    let (client, mut frames) = Client::stub_silent();
    let change = |enabled| SkillChange::SetEnabled {
        name: "lint".to_owned(),
        enabled,
    };
    let changes = [change(false), change(true)];
    let outcome = send(&client, "s", &changes, Duration::from_millis(10)).await;
    assert!(outcome.error.is_some());
    assert!(frames.try_recv().is_ok(), "the first change was sent");
    assert!(frames.try_recv().is_err(), "the second one never was");
}
