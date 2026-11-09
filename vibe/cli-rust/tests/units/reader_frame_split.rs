//! Newline frame splitting keeps the scan linear across partial reads.

use vibe_rs::server::reader::next_frame;

#[test]
fn next_frame_resumes_scan_after_partial_reads() {
    let mut tail = Vec::new();
    let mut searched = 0;
    // First read: partial frame, no newline yet.
    tail.extend_from_slice(b"{\"id\":");
    assert!(next_frame(&mut tail, &mut searched).is_none());
    assert_eq!(searched, tail.len());
    // Second read completes the frame; the scan must skip the bytes
    // already examined above instead of rescanning from index 0.
    tail.extend_from_slice(b"1}\n");
    assert_eq!(
        next_frame(&mut tail, &mut searched).as_deref(),
        Some("{\"id\":1}")
    );
    assert_eq!(searched, 0);
    assert!(tail.is_empty());
}
