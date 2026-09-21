//! Deleted-working-directory startup behavior.

#![cfg(unix)]

use std::fs;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use vibe_rs::cli::{resolve_working_directory, WorkingDirectoryError};

#[test]
fn deleted_working_directory_is_guarded_and_explicit_cwd_recovers() {
    static NONCE: AtomicU64 = AtomicU64::new(0);
    let counter = NONCE.fetch_add(1, Ordering::Relaxed);
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let deleted = std::env::temp_dir().join(format!(
        "vibe-rs-deleted-cwd-{}-{nonce}-{counter}",
        std::process::id()
    ));
    fs::create_dir(&deleted).unwrap();

    let output = Command::new("/bin/sh")
        .args([
            "-c",
            "cd \"$1\" && rmdir \"$1\" && exec \"$2\"",
            "vibe-rs-deleted-cwd-test",
        ])
        .arg(&deleted)
        .arg(env!("CARGO_BIN_EXE_vibe-rs"))
        .env_remove("VIBE_CWD")
        .env("RUST_BACKTRACE", "1")
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).unwrap(),
        "Error: Current working directory no longer exists.\n\n\
The directory you started vibe from has been deleted. Please change to an existing directory \
and try again, or use --workdir to specify a working directory.\n"
    );

    let original = std::env::current_dir().unwrap();
    let root = std::env::temp_dir().join(format!(
        "vibe-rs-cwd-recovery-{}-{nonce}",
        std::process::id()
    ));
    let deleted = root.join("deleted");
    let replacement = root.join("replacement");
    fs::create_dir_all(&deleted).unwrap();
    fs::create_dir(&replacement).unwrap();
    let expected = replacement.canonicalize().unwrap();
    let requested = replacement.join("..").join("replacement");
    std::env::set_current_dir(&deleted).unwrap();
    fs::remove_dir(&deleted).unwrap();

    let resolved = resolve_working_directory(Some(requested)).unwrap();

    assert_eq!(resolved, expected);
    assert_eq!(std::env::current_dir().unwrap(), expected);

    let missing = root.join("missing");
    let error = resolve_working_directory(Some(missing.clone())).unwrap_err();
    assert!(matches!(
        error,
        WorkingDirectoryError::Override { path, source }
            if path == missing && source.kind() == std::io::ErrorKind::NotFound
    ));

    let file = root.join("file");
    fs::write(&file, b"not a directory").unwrap();
    let error = resolve_working_directory(Some(file.clone())).unwrap_err();
    assert!(matches!(
        error,
        WorkingDirectoryError::Override { path, source }
            if path == file && source.kind() == std::io::ErrorKind::NotADirectory
    ));

    std::env::set_current_dir(original).unwrap();
    for _ in 0..3 {
        if fs::remove_dir_all(&root).is_ok() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
}
