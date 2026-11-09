//! The queue spacer shifts only the queued run and what follows it.

use vibe_rs::utils::transcript_cache::{QueueSpacer, TranscriptCache, TranscriptLayout};

#[test]
fn rows_above_the_queue_stay_put() {
    let spacer = QueueSpacer { at: 10, height: 5 };
    assert_eq!(spacer.offset(0), 0);
    assert_eq!(spacer.offset(9), 9);
}

#[test]
fn the_queue_and_rows_below_it_move_down() {
    let spacer = QueueSpacer { at: 10, height: 5 };
    assert_eq!(spacer.offset(10), 15);
    assert_eq!(spacer.offset(12), 17);
}

#[test]
fn a_layout_without_a_queue_has_no_spacer() {
    let layout = TranscriptLayout::default();
    let spacer = layout.queue_spacer(7);
    assert_eq!(spacer.height, 0);
    assert_eq!(spacer.offset(3), 3);
}

#[test]
fn a_layout_with_a_queue_fills_the_free_rows_above_it() {
    let mut cache = TranscriptCache::default();
    cache.ensure_context(40, 39, 0, false);
    cache.store_layout(1, 40, 10, Vec::new(), Some(4));
    let spacer = cache.layout(1, 40).unwrap().queue_spacer(7);
    assert_eq!((spacer.at, spacer.height), (4, 7));
}
