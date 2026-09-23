//! `RotatingFile` mirrors `RotatingFileHandler(backupCount=0)`: truncate in
//! place at the cap, keep no backup.

use vibe_rs::observability::rotating::RotatingFile;

fn temp_path(name: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("vibe-rs-rotate-{}-{name}.log", std::process::id()))
}

#[test]
fn truncates_in_place_once_the_cap_is_crossed() {
    let path = temp_path("cap");
    let _ = std::fs::remove_file(&path);
    // Room for one 9-byte line ("aaaaaaaa\n") but not two.
    let mut file = RotatingFile::open(&path, 16).unwrap();
    file.write_line("aaaaaaaa");
    file.write_line("bbbbbbbb");
    drop(file);

    let body = std::fs::read_to_string(&path).unwrap();
    assert_eq!(body, "bbbbbbbb\n");
    assert!(!path.with_extension("log.1").exists());
    let _ = std::fs::remove_file(&path);
}

#[test]
fn appends_to_an_existing_file_and_creates_missing_parents() {
    let dir = temp_path("nested").with_extension("d");
    let path = dir.join("logs").join("vibe-rs.log");
    let _ = std::fs::remove_dir_all(&dir);

    let mut file = RotatingFile::open(&path, 0).unwrap();
    file.write_line("first");
    drop(file);
    let mut file = RotatingFile::open(&path, 0).unwrap();
    file.write_line("second");
    drop(file);

    assert_eq!(std::fs::read_to_string(&path).unwrap(), "first\nsecond\n");
    let _ = std::fs::remove_dir_all(&dir);
}
