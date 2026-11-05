//! `/context` gauge layout: segments, proportional slots, the threshold position, wire parsing.

use vibe_rs::server::{ContextBreakdown, ContextCategory, ContextCategoryId};
use vibe_rs::ui::context_bar::{allocate, buffer_start, percent, segments, slots, Segment};

fn breakdown(used: u64, cats: &[(ContextCategoryId, u64)]) -> ContextBreakdown {
    ContextBreakdown {
        context_window: 200_000,
        compaction_threshold: 150_000,
        used_tokens: used,
        measured: true,
        total_characters: 0,
        categories: cats
            .iter()
            .map(|(id, tokens)| ContextCategory {
                id: Some(*id),
                characters: 0,
                estimated_tokens: *tokens,
            })
            .collect(),
    }
}

#[test]
fn segments_are_categories_then_free_then_buffer() {
    let b = breakdown(
        40_000,
        &[
            (ContextCategoryId::System, 10_000),
            (ContextCategoryId::Conversation, 30_000),
        ],
    );
    assert_eq!(
        segments(&b),
        vec![
            (Segment::Category(ContextCategoryId::System), 10_000),
            (Segment::Category(ContextCategoryId::Conversation), 30_000),
            (Segment::Free, 110_000),
            (Segment::Buffer, 50_000),
        ]
    );
}

#[test]
fn an_unmeasured_window_is_only_free_space_and_buffer() {
    let mut b = breakdown(0, &[(ContextCategoryId::System, 5)]);
    b.measured = false;
    assert_eq!(
        segments(&b),
        vec![(Segment::Free, 150_000), (Segment::Buffer, 50_000)]
    );
}

#[test]
fn usage_past_the_threshold_eats_into_the_buffer() {
    let b = breakdown(180_000, &[(ContextCategoryId::Conversation, 180_000)]);
    assert_eq!(
        segments(&b),
        vec![
            (Segment::Category(ContextCategoryId::Conversation), 180_000),
            (Segment::Buffer, 20_000),
        ]
    );
}

#[test]
fn allocation_is_proportional_and_exact() {
    assert_eq!(allocate(&[50, 25, 25], 8), vec![4, 2, 2]);
    assert_eq!(allocate(&[1, 1, 1], 10).iter().sum::<usize>(), 10);
    assert_eq!(allocate(&[0, 0], 10), vec![0, 0]);
}

#[test]
fn a_tiny_share_still_gets_one_slot() {
    let alloc = allocate(&[1, 1_000_000], 20);
    assert_eq!(alloc, vec![1, 19]);
}

#[test]
fn slots_fill_left_to_right_and_mark_the_buffer() {
    let b = breakdown(50_000, &[(ContextCategoryId::System, 50_000)]);
    let bar = slots(&b, 8);
    assert_eq!(bar.len(), 8);
    assert_eq!(bar[0], Segment::Category(ContextCategoryId::System));
    assert_eq!(bar[1], Segment::Category(ContextCategoryId::System));
    assert_eq!(bar[2], Segment::Free);
    assert_eq!(bar[6], Segment::Buffer);
    // The buffer starts at slot 6, i.e. cell 3 of a 4-cell gauge.
    assert_eq!(buffer_start(&bar), Some(3));
}

#[test]
fn percent_rounds() {
    assert_eq!(percent(42_300, 200_000), 21);
    assert_eq!(percent(999, 200_000), 0);
    assert_eq!(percent(1, 0), 100);
}

#[test]
fn wire_parsing_tolerates_missing_fields_and_unknown_categories() {
    let empty: ContextBreakdown = serde_json::from_str("{}").unwrap();
    assert_eq!(empty, ContextBreakdown::default());
    let parsed: ContextBreakdown = serde_json::from_str(
        r#"{"contextWindow": 200000, "usedTokens": 10, "measured": true,
            "categories": [{"id": "images", "estimatedTokens": 2},
                           {"id": "tools", "estimatedTokens": 3},
                           {"id": "system", "estimatedTokens": 5}]}"#,
    )
    .unwrap();
    assert_eq!(parsed.categories[0].id, Some(ContextCategoryId::Unknown));
    assert_eq!(parsed.categories[1].id, Some(ContextCategoryId::Tools));
    assert_eq!(parsed.categories[2].id, Some(ContextCategoryId::System));
    assert_eq!(parsed.categories[2].characters, 0);
}

#[test]
fn a_measured_total_without_a_split_is_one_unsplit_segment() {
    let b = breakdown(40_000, &[]);
    assert_eq!(
        segments(&b),
        vec![
            (Segment::Unsplit, 40_000),
            (Segment::Free, 110_000),
            (Segment::Buffer, 50_000),
        ]
    );
}
