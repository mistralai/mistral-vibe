//! Settled-burst draining: coalesce queued values until quiet, capped.

use std::sync::mpsc;
use std::thread::sleep;
use std::time::{Duration, Instant};

use vibe_rs::utils::settle::drain_settled;

fn send_spaced(tx: &mpsc::SyncSender<u32>, count: u32, gap: Duration) {
    for value in 1..=count {
        let _ = tx.send(value);
        sleep(gap);
    }
}

#[test]
fn a_burst_that_goes_quiet_collects_every_value() {
    let (tx, rx) = mpsc::sync_channel(16);
    send_spaced(&tx, 3, Duration::from_millis(5));
    let burst = drain_settled(&rx, 0, Duration::from_millis(60), Duration::from_secs(5));
    assert_eq!(burst, vec![0, 1, 2, 3]);
}

#[test]
fn a_lone_value_settles_by_itself() {
    let (_tx, rx) = mpsc::sync_channel::<u32>(16);
    let burst = drain_settled(&rx, 7, Duration::from_millis(20), Duration::from_secs(5));
    assert_eq!(burst, vec![7]);
}

#[test]
fn a_burst_that_never_settles_is_capped_by_its_first_value() {
    let (tx, rx) = mpsc::sync_channel(1024);
    let sender = std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_millis(400);
        let mut value = 0;
        while Instant::now() < deadline {
            let _ = tx.send(value);
            value += 1;
            sleep(Duration::from_millis(5));
        }
        value
    });
    let start = Instant::now();
    let burst = drain_settled(&rx, 0, Duration::from_millis(60), Duration::from_millis(80));
    let elapsed = start.elapsed();
    assert!(burst.len() > 1, "a busy burst collects more than its seed");
    assert!(
        elapsed < Duration::from_millis(500),
        "the cap ends a busy burst promptly, took {elapsed:?}"
    );
    assert!(
        sender.join().is_ok(),
        "the sender outlives the capped burst"
    );
}
