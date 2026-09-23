//! Filtered Crossterm events with bounded backpressure.

use std::time::{Duration, Instant};

use crossterm::event::{Event, EventStream};
use futures::StreamExt;
use tokio::sync::mpsc;

use crate::terminal_input_filter::TerminalInputFilter;

const CHANNEL_CAP: usize = 256;

pub fn spawn() -> mpsc::Receiver<Event> {
    let (tx, rx) = mpsc::channel(CHANNEL_CAP);
    tokio::spawn(async move {
        let mut events = EventStream::new();
        let mut filter = TerminalInputFilter::default();
        let mut flush = tokio::time::interval(Duration::from_millis(100));
        flush.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            let pending = tokio::select! {
                biased;
                _ = tx.closed() => return,
                _ = flush.tick() => filter.flush_expired(Instant::now()),
                event = events.next() => match event {
                    Some(Ok(event)) => filter.push(event),
                    Some(Err(error)) => {
                        tracing::warn!(%error, "terminal event stream failed");
                        return;
                    }
                    None => return,
                },
            };
            for event in pending {
                if tx.send(event).await.is_err() {
                    return;
                }
            }
        }
    });
    rx
}
