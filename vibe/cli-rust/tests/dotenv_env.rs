//! The client's `.env` load mutates the client's own environment and records
//! exactly which keys it set, so `Client::spawn` can strip them from the
//! app-server child — the child re-derives the file with python-dotenv.
//! `std::env` is process-global: its own test binary, one test at a time.

use std::sync::Mutex;

/// Serializes the env-mutating test against any other in this binary.
static ENV_LOCK: Mutex<()> = Mutex::new(());

/// A temp `VIBE_HOME` whose `.env` holds `content`.
fn write_env(content: &str) -> tempfile::TempDir {
    let home = tempfile::tempdir().expect("temp home");
    std::fs::write(home.path().join(".env"), content).expect("write .env");
    std::env::set_var("VIBE_HOME", home.path());
    home
}

#[test]
fn the_load_records_exactly_the_keys_it_set() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    // A non-empty shell value wins over the file and is never recorded; an
    // empty one lets the file through (Python `load_dotenv_values`).
    std::env::set_var("DOTENV_TEST_SHELL", "from-shell");
    std::env::set_var("DOTENV_TEST_EMPTY", "");
    let home = write_env(
        "DOTENV_TEST_SHELL=from-file\nDOTENV_TEST_EMPTY=from-file\nDOTENV_TEST_NEW=from-file\n",
    );
    vibe_rs::credentials::dotenv::load_dotenv_values();

    assert_eq!(std::env::var("DOTENV_TEST_SHELL").unwrap(), "from-shell");
    assert_eq!(std::env::var("DOTENV_TEST_EMPTY").unwrap(), "from-file");
    assert_eq!(std::env::var("DOTENV_TEST_NEW").unwrap(), "from-file");
    assert_eq!(
        vibe_rs::credentials::dotenv::dotenv_set_keys(),
        vec!["DOTENV_TEST_EMPTY".to_owned(), "DOTENV_TEST_NEW".to_owned(),]
    );

    std::env::remove_var("VIBE_HOME");
    for key in ["DOTENV_TEST_SHELL", "DOTENV_TEST_EMPTY", "DOTENV_TEST_NEW"] {
        std::env::remove_var(key);
    }
    drop(home);
}

#[test]
fn an_absent_or_unreadable_env_file_records_nothing() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    let home = tempfile::tempdir().expect("temp home");
    std::env::set_var("VIBE_HOME", home.path());
    vibe_rs::credentials::dotenv::load_dotenv_values();

    assert!(vibe_rs::credentials::dotenv::dotenv_set_keys().is_empty());

    std::env::remove_var("VIBE_HOME");
}

#[test]
fn a_miss_after_a_load_clears_the_record() {
    // Every load defines the record: a later unreadable file must not
    // leave the previous load's keys behind (the record decides what
    // `Client::spawn` strips from the child).
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    let loaded = write_env("DOTENV_TEST_ORDER=from-file\n");
    vibe_rs::credentials::dotenv::load_dotenv_values();
    assert_eq!(
        vibe_rs::credentials::dotenv::dotenv_set_keys(),
        vec!["DOTENV_TEST_ORDER".to_owned()]
    );

    let missed = tempfile::tempdir().expect("temp home");
    std::env::set_var("VIBE_HOME", missed.path());
    vibe_rs::credentials::dotenv::load_dotenv_values();
    assert!(vibe_rs::credentials::dotenv::dotenv_set_keys().is_empty());

    std::env::remove_var("VIBE_HOME");
    std::env::remove_var("DOTENV_TEST_ORDER");
    drop(loaded);
    drop(missed);
}
