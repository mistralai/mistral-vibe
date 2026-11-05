//! Under replay the UI clock stands still until advanced, and UI deadlines wake on the advance.

use std::time::Duration;

use vibe_rs::clock;

/// Each isolated test owns its process, so the clock reads this before anything else.
fn freeze() {
    std::env::set_var("VIBE_REPLAY_FIXTURE", "replay-clock-test");
}

#[test]
fn replay_time_only_moves_when_advanced() {
    freeze();
    let start = clock::now();
    std::thread::sleep(Duration::from_millis(20));
    assert_eq!(clock::now(), start, "wall time does not reach the UI clock");
    clock::advance(Duration::from_millis(300));
    assert_eq!(clock::since(start), Duration::from_millis(300));
}

#[tokio::test]
async fn a_ui_deadline_fires_on_the_advance_that_reaches_it() {
    freeze();
    let due = clock::now() + Duration::from_millis(500);
    let sleeper = tokio::spawn(clock::sleep_until(Some(due)));
    clock::advance(Duration::from_millis(400));
    tokio::time::sleep(Duration::from_millis(20)).await;
    assert!(!sleeper.is_finished(), "400 ms of 500 have passed");
    clock::advance(Duration::from_millis(100));
    tokio::time::timeout(Duration::from_secs(5), sleeper)
        .await
        .expect("the advance wakes the deadline")
        .unwrap();
}

#[tokio::test]
async fn an_animation_ticks_once_per_advance_that_reaches_it() {
    freeze();
    let mut blink = clock::interval(Duration::from_millis(500));
    std::thread::sleep(Duration::from_millis(20));
    assert!(!blink.is_due(), "wall time does not tick an animation");
    clock::advance(Duration::from_millis(1200));
    assert!(blink.is_due());
    tokio::time::timeout(Duration::from_secs(5), blink.tick())
        .await
        .expect("the advance made the tick due");
    assert!(!blink.is_due(), "ticks missed meanwhile are skipped");
    clock::advance(Duration::from_millis(500));
    assert!(blink.is_due());
}
