//! The release-notes history: the embedded files, the unseen slice, the startup banner and the release-notes filter.

use vibe_rs::update_notifier::version::parse_version;
use vibe_rs::whats_new::{self, Entry, MAX_STARTUP_ENTRIES};

fn entry(version: &str, body: &str) -> Entry {
    Entry {
        version: version.to_owned(),
        body: body.to_owned(),
    }
}

fn history() -> Vec<Entry> {
    vec![
        entry("3.0.2", "- **Selection**: native"),
        entry("3.0.0", "- **Rust**: new UI"),
        entry("2.26.0", "- **Background**: processes"),
        entry("2.9.1", "- **Default model**: medium"),
    ]
}

fn versions(entries: &[Entry]) -> Vec<&str> {
    entries.iter().map(|entry| entry.version.as_str()).collect()
}

#[test]
fn the_embedded_history_is_sorted_newest_first_and_never_ahead_of_the_build() {
    let entries = whats_new::entries();
    assert!(entries.len() > 1);
    let parsed: Vec<_> = entries
        .iter()
        .map(|entry| parse_version(&entry.version).expect("X.Y.Z file name"))
        .collect();
    assert!(parsed.windows(2).all(|pair| pair[0] > pair[1]));
    assert!(parsed[0] <= parse_version(env!("CARGO_PKG_VERSION")).unwrap());
    assert!(entries.iter().all(|entry| !entry.body.trim().is_empty()));
}

#[test]
fn unseen_keeps_every_release_newer_than_the_seen_one() {
    let entries = history();
    assert_eq!(
        versions(whats_new::unseen(&entries, Some("2.9.1"))),
        ["3.0.2", "3.0.0", "2.26.0"]
    );
    assert_eq!(
        versions(whats_new::unseen(&entries, Some("2.26.1"))),
        ["3.0.2", "3.0.0"]
    );
    assert!(whats_new::unseen(&entries, Some("3.0.2")).is_empty());
    assert!(whats_new::unseen(&entries, Some("9.0.0")).is_empty());
}

#[test]
fn unseen_without_a_valid_seen_version_shows_only_the_newest() {
    let entries = history();
    assert_eq!(versions(whats_new::unseen(&entries, None)), ["3.0.2"]);
    assert_eq!(
        versions(whats_new::unseen(&entries, Some("garbage"))),
        ["3.0.2"]
    );
    assert!(whats_new::unseen(&[], None).is_empty());
}

#[test]
fn the_startup_body_heads_each_release_with_its_version() {
    let entries = history();
    assert_eq!(
        whats_new::startup_body(&entries[..1]).as_deref(),
        Some(
            "**v3.0.2**\n\n- **Selection**: native\n\n\
             _Browse every release with `/release-notes`._"
        )
    );
    assert_eq!(
        whats_new::startup_body(&entries[..2]).as_deref(),
        Some(
            "**v3.0.2**\n\n- **Selection**: native\n\n\
             **v3.0.0**\n\n- **Rust**: new UI\n\n\
             _Browse every release with `/release-notes`._"
        )
    );
    assert_eq!(whats_new::startup_body(&[]), None);
}

#[test]
fn the_startup_body_caps_the_releases_and_counts_the_rest() {
    let entries: Vec<Entry> = (0..MAX_STARTUP_ENTRIES + 2)
        .map(|minor| entry(&format!("2.{}.0", 30 - minor), "- note"))
        .collect();
    let body = whats_new::startup_body(&entries).expect("releases");
    assert_eq!(body.matches("**v2.").count(), MAX_STARTUP_ENTRIES);
    assert!(body
        .ends_with("_2 earlier releases not shown: browse every release with `/release-notes`._"));

    let body = whats_new::startup_body(&entries[..MAX_STARTUP_ENTRIES + 1]).expect("releases");
    assert!(body
        .ends_with("_1 earlier release not shown: browse every release with `/release-notes`._"));
}

#[test]
fn the_release_notes_list_every_release_and_filters_by_version_or_text() {
    let entries = history();
    let all = whats_new::release_notes(&entries, "");
    assert!(all.starts_with("### v3.0.2\n\n- **Selection**: native\n\n### v3.0.0"));
    assert_eq!(all.matches("### v").count(), 4);

    assert_eq!(
        whats_new::release_notes(&entries, "  RUST "),
        "### v3.0.0\n\n- **Rust**: new UI"
    );
    assert_eq!(
        whats_new::release_notes(&entries, "2.9"),
        "### v2.9.1\n\n- **Default model**: medium"
    );
    assert_eq!(
        whats_new::release_notes(&entries, "V2.9.1"),
        "### v2.9.1\n\n- **Default model**: medium"
    );
    assert_eq!(whats_new::release_notes(&entries, "nothing like this"), "");
}

#[test]
fn only_a_lone_x_y_z_version_reads_as_a_release_heading() {
    assert!(whats_new::is_version_heading("v3.0.3"));
    assert!(!whats_new::is_version_heading("v3.0"));
    assert!(!whats_new::is_version_heading("3.0.3"));
    assert!(!whats_new::is_version_heading("• v3.0.3 brings a pager"));
    assert!(!whats_new::is_version_heading("vibe"));
}
