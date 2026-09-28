//! Prepared Markdown and transcript geometry cache contracts.

use std::cell::Cell;
use std::sync::Arc;

use serde_json::json;
use vibe_rs::transcript::Transcript;
use vibe_rs::ui::markdown::{
    MarkdownCache, PreparedMarkdown, MAX_MARKDOWN_CACHE_BYTES, MAX_MARKDOWN_CACHE_ENTRIES,
};
use vibe_rs::utils::transcript_cache::{EntryGeometry, TranscriptCache};

const TABLE: &str = "| Feature | Description |\n|---|---|\n| Cache | Reuses wrapped table layout |";

fn assistant_transcript(text: &str) -> Transcript {
    let mut transcript = Transcript::default();
    transcript.add(&json!({
        "entry": {
            "id": "assistant",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "generationStatus": "in_progress",
        }
    }));
    transcript
}

fn update_assistant_text(transcript: &mut Transcript, operation: &str, text: &str) {
    transcript.update(&json!({
        "entryId": "assistant",
        "patch": [{"op": operation, "path": "/content/0/text", "value": text}],
    }));
}

fn rendered_text(prepared: &PreparedMarkdown) -> String {
    prepared
        .lines()
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n")
}

fn table_with_cell(bytes: usize) -> String {
    format!("| Value |\n|---|\n| {} |", "x".repeat(bytes))
}

#[test]
fn prepared_markdown_is_reused_only_for_the_stable_render_key() {
    let calls = Cell::new(0);
    let mut stable = MarkdownCache::default();
    let prepared = stable.prepare(3, 7, 80, 2, || {
        calls.set(calls.get() + 1);
        TABLE.to_owned()
    });
    let hit = stable.prepare(3, 7, 80, 2, || {
        calls.set(calls.get() + 1);
        TABLE.to_owned()
    });
    assert!(Arc::ptr_eq(&prepared, &hit));
    assert_eq!(calls.get(), 1);

    for changed in [(4, 7, 80, 2), (3, 8, 80, 2), (3, 7, 79, 2), (3, 7, 80, 1)] {
        let mut cache = MarkdownCache::default();
        let original = cache.prepare(3, 7, 80, 2, || TABLE.to_owned());
        let changed = cache.prepare(changed.0, changed.1, changed.2, changed.3, || {
            TABLE.to_owned()
        });
        assert!(!Arc::ptr_eq(&original, &changed));
    }
}

#[test]
fn assistant_delta_bumps_entry_revision() {
    let mut transcript = assistant_transcript("before");
    let before = transcript.entry(0).expect("assistant entry exists").rev;

    update_assistant_text(&mut transcript, "append", " after");

    let after = transcript.entry(0).expect("assistant entry exists").rev;
    assert!(after > before);
    assert_eq!(
        transcript.last_assistant_message().as_deref(),
        Some("before after")
    );
}

#[test]
fn prepared_markdown_recomputes_content_after_revision_bump() {
    let mut transcript = assistant_transcript("before");
    let mut cache = MarkdownCache::default();
    let entry = transcript.entry(0).expect("assistant entry exists");
    let before = cache.prepare(entry.index, entry.rev, 80, 0, || {
        transcript
            .last_assistant_message()
            .expect("assistant text exists")
    });

    update_assistant_text(&mut transcript, "replace", "after");
    let entry = transcript.entry(0).expect("assistant entry exists");
    let after = cache.prepare(entry.index, entry.rev, 80, 0, || {
        transcript
            .last_assistant_message()
            .expect("assistant text exists")
    });

    assert!(rendered_text(&before).contains("before"));
    assert!(rendered_text(&after).contains("after"));
    assert!(!rendered_text(&after).contains("before"));
}

#[test]
fn prepared_markdown_cache_evicts_old_entries_at_its_bound() {
    let mut cache = MarkdownCache::default();
    let first = cache.prepare(0, 1, 80, 0, || TABLE.to_owned());
    for entry in 1..=MAX_MARKDOWN_CACHE_ENTRIES {
        cache.prepare(entry, 1, 80, 0, || TABLE.to_owned());
    }

    assert!(!Arc::ptr_eq(
        &first,
        &cache.prepare(0, 1, 80, 0, || TABLE.to_owned())
    ));
}

#[test]
fn prepared_markdown_cache_evicts_old_entries_at_its_byte_bound() {
    let source = table_with_cell(MAX_MARKDOWN_CACHE_BYTES / 100);
    let mut cache = MarkdownCache::default();
    let first = cache.prepare(0, 1, u16::MAX, 0, || source.clone());
    let hit = cache.prepare(0, 1, u16::MAX, 0, || source.clone());
    assert!(Arc::ptr_eq(&first, &hit));

    for entry in 1..MAX_MARKDOWN_CACHE_ENTRIES {
        cache.prepare(entry, 1, u16::MAX, 0, || source.clone());
    }

    assert!(!Arc::ptr_eq(
        &first,
        &cache.prepare(0, 1, u16::MAX, 0, || source.clone())
    ));
}

#[test]
fn oversized_prepared_markdown_is_not_retained() {
    let source = table_with_cell(MAX_MARKDOWN_CACHE_BYTES / 2);
    let calls = Cell::new(0);
    let mut cache = MarkdownCache::default();
    let first = cache.prepare(0, 1, u16::MAX, 0, || {
        calls.set(calls.get() + 1);
        source.clone()
    });
    let second = cache.prepare(0, 1, u16::MAX, 0, || {
        calls.set(calls.get() + 1);
        source.clone()
    });

    assert!(!Arc::ptr_eq(&first, &second));
    assert_eq!(calls.get(), 2);
}

#[test]
fn newer_revision_removes_stale_prepared_markdown() {
    let calls = Cell::new(0);
    let mut cache = MarkdownCache::default();
    for revision in [1, 2, 1] {
        cache.prepare(0, revision, 80, 0, || {
            calls.set(calls.get() + 1);
            TABLE.to_owned()
        });
    }

    assert_eq!(calls.get(), 3);
}

#[test]
fn transcript_geometry_reuses_the_prewrapped_verdict() {
    let mut cache = TranscriptCache::default();
    let computes = Cell::new(0);
    cache.ensure_context(80, 79, 2, false);

    let first = cache.geometry(0, 1, 80, || {
        computes.set(computes.get() + 1);
        EntryGeometry {
            height: 12,
            prewrapped: true,
        }
    });
    let hit = cache.geometry(0, 1, 80, || {
        computes.set(computes.get() + 1);
        EntryGeometry {
            height: 99,
            prewrapped: false,
        }
    });

    assert_eq!(hit.height, first.height);
    assert_eq!(hit.prewrapped, first.prewrapped);
    assert_eq!(computes.get(), 1);
}

#[test]
fn transcript_geometry_is_invalidated_by_width_theme_and_queue_copy() {
    let mut cache = TranscriptCache::default();
    let computes = Cell::new(0);
    let geometry = || {
        computes.set(computes.get() + 1);
        EntryGeometry {
            height: 12,
            prewrapped: true,
        }
    };

    cache.ensure_context(80, 79, 2, false);
    assert_eq!(cache.geometry(0, 1, 80, geometry).height, 12);
    assert_eq!(cache.geometry(0, 1, 80, geometry).height, 12);
    cache.ensure_context(79, 78, 2, false);
    assert_eq!(cache.geometry(0, 1, 79, geometry).height, 12);
    cache.ensure_context(79, 78, 3, false);
    assert_eq!(cache.geometry(0, 1, 79, geometry).height, 12);
    cache.ensure_context(79, 78, 3, true);
    assert_eq!(cache.geometry(0, 1, 79, geometry).height, 12);

    assert_eq!(computes.get(), 4);
}

#[test]
fn effect_body_rows_are_built_once_per_render_key() {
    let calls = Cell::new(0);
    let mut cache = MarkdownCache::default();
    let build = || {
        calls.set(calls.get() + 1);
        vec![ratatui::text::Line::from("+ row")]
    };
    let first = cache.prepare_lines(5, 9, 80, 1, build);
    let hit = cache.prepare_lines(5, 9, 80, 1, build);
    assert!(Arc::ptr_eq(&first, &hit));
    assert_eq!(rendered_text(&hit), "+ row");
    cache.prepare_lines(5, 10, 80, 1, build);
    assert_eq!(calls.get(), 2);
}
