//! History persistence: merge, read, round-trip, and failure-path behavior.

use std::fs;

use vibe_rs::utils::history_persist::{merge_entries, read_entries, Persister, MAX_ENTRIES};

#[test]
fn merge_appends_pending_after_disk() {
    let merged = merge_entries(vec!["a".into()], &["b".into(), "c".into()], 10);
    assert_eq!(merged, vec!["a".to_owned(), "b".to_owned(), "c".to_owned()]);
}

#[test]
fn merge_dedups_at_the_seam() {
    let merged = merge_entries(vec!["a".into(), "b".into()], &["b".into(), "c".into()], 10);
    assert_eq!(merged, vec!["a".to_owned(), "b".to_owned(), "c".to_owned()]);
}

#[test]
fn merge_keeps_the_newest_max_entries() {
    let disk: Vec<String> = (0..98).map(|i| i.to_string()).collect();
    let pending = ["98".to_owned(), "99".to_owned(), "100".to_owned()];
    let merged = merge_entries(disk, &pending, 100);
    assert_eq!(merged.len(), 100);
    assert_eq!(merged.first().map(String::as_str), Some("1"));
    assert_eq!(merged.last().map(String::as_str), Some("100"));
}

#[test]
fn merge_with_empty_pending_is_a_no_op() {
    let merged = merge_entries(vec!["a".into(), "b".into()], &[], 10);
    assert_eq!(merged, vec!["a".to_owned(), "b".to_owned()]);
}

#[test]
fn read_entries_parses_json_per_line_and_keeps_bad_lines_verbatim() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    fs::write(&file, "\"one\"\nnot json\n\"two\"\n\n").unwrap();
    assert_eq!(
        read_entries(&file),
        vec!["one".to_owned(), "not json".to_owned(), "two".to_owned()]
    );
}

#[test]
fn read_entries_keeps_the_newest_max_entries() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    let body: String = (0..(MAX_ENTRIES + 5))
        .map(|i| format!("\"{i}\"\n"))
        .collect();
    fs::write(&file, body).unwrap();
    let entries = read_entries(&file);
    assert_eq!(entries.len(), MAX_ENTRIES);
    assert_eq!(entries.first().map(String::as_str), Some("5"));
    assert_eq!(entries.last().map(String::as_str), Some("104"));
}

#[test]
fn read_entries_missing_file_is_empty() {
    let dir = tempfile::tempdir().unwrap();
    assert!(read_entries(&dir.path().join("vibehistory")).is_empty());
}

#[test]
fn flush_merges_pending_onto_another_sessions_file() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    fs::write(&file, "\"disk-a\"\n\"disk-b\"\n").unwrap();
    let persister = Persister::new(Some(file.clone()));
    persister.record("disk-b"); // seam duplicate with the other session's last
    persister.record("mine-a");
    persister.flush();
    assert_eq!(persister.pending_len(), 0);
    assert_eq!(
        read_entries(&file),
        vec![
            "disk-a".to_owned(),
            "disk-b".to_owned(),
            "mine-a".to_owned()
        ]
    );
}

#[test]
fn flush_without_pending_leaves_the_file_untouched() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    fs::write(&file, "\"disk-a\"\n").unwrap();
    Persister::new(Some(file.clone())).flush();
    assert_eq!(read_entries(&file), vec!["disk-a".to_owned()]);
}

#[test]
fn flush_failure_preserves_pending_and_retries_later() {
    let dir = tempfile::tempdir().unwrap();
    let blocker = dir.path().join("blocker");
    fs::write(&blocker, "not a directory").unwrap();
    // Parent is a file, so the atomic write must fail without panicking.
    let persister = Persister::new(Some(blocker.join("vibehistory")));
    persister.record("mine-a");
    persister.flush();
    assert_eq!(persister.pending_len(), 1);
    // A writable target flushes the same pending queue successfully.
    let good = Persister::new(Some(dir.path().join("vibehistory")));
    good.record("mine-a");
    good.flush();
    assert_eq!(good.pending_len(), 0);
    assert_eq!(
        read_entries(&dir.path().join("vibehistory")),
        vec!["mine-a".to_owned()]
    );
}

#[test]
fn flush_keeps_pending_when_the_file_cannot_be_read() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    fs::create_dir(&file).unwrap();
    let persister = Persister::new(Some(file.clone()));
    persister.record("mine-a");
    persister.flush();
    assert_eq!(persister.pending_len(), 1);
    assert!(file.is_dir());
}

#[test]
fn flush_failure_leaves_no_temp_file_behind() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    // The target is a directory, so the temp file is written but the rename fails.
    fs::create_dir(&file).unwrap();
    let persister = Persister::new(Some(file.clone()));
    persister.record("mine-a");
    persister.flush();
    assert_eq!(persister.pending_len(), 1);
    let tmp = dir
        .path()
        .join(format!(".vibehistory.{}.tmp", std::process::id()));
    assert!(!tmp.exists());
}

#[test]
fn flush_does_not_touch_another_process_temp_file() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("vibehistory");
    let foreign = dir
        .path()
        .join(format!(".vibehistory.{}.tmp", std::process::id() + 1));
    fs::write(&foreign, "in flight").unwrap();
    let persister = Persister::new(Some(file.clone()));
    persister.record("mine-a");
    persister.flush();
    assert_eq!(read_entries(&file), vec!["mine-a".to_owned()]);
    assert_eq!(fs::read_to_string(&foreign).unwrap(), "in flight");
}
